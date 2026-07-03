"""
Minimal client for the Kustodyan / RegData Protection Suite (RPS) Engine, used by the
paperless-ngx integration to protect/unprotect custom-field values.

stdlib-only (urllib) so it adds no dependency to the paperless image. The transform
behaviour (encrypt / tokenize / mask) is server-configured and selected by the evidence
sent: a Role (who) and an Action (Protect/Unprotect). className/propertyName address the
configured data instance.
"""
import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from threading import Lock


class RpsError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class RpsClient:
    def __init__(self, identity_url=None, engine_url=None, client_id=None,
                 client_secret=None, default_class=None):
        self.identity_url = (identity_url or os.environ["KUSTODYAN_IDENTITY_URL"]).rstrip("/")
        self.engine_url = (engine_url or os.environ["KUSTODYAN_ENGINE_URL"]).rstrip("/")
        self.client_id = client_id or os.environ["KUSTODYAN_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["KUSTODYAN_CLIENT_SECRET"]
        self.default_class = default_class or os.environ.get(
            "KUSTODYAN_DEFAULT_CLASSNAME", "ch.kyos.Contact")
        self.auth_path = os.environ.get("KUSTODYAN_AUTH_PATH", "connect/token")
        self.transform_path = os.environ.get("KUSTODYAN_TRANSFORM_PATH", "transform")
        self._token = None
        self._exp = 0
        self._lock = Lock()

    # --- low-level HTTP ---
    @staticmethod
    def _post(url, data, headers):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _get_token(self, force=False):
        with self._lock:
            now = time.time()
            if not force and self._token and now < self._exp - 30:
                return self._token
            body = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }).encode()
            st, raw = self._post(f"{self.identity_url}/{self.auth_path}", body,
                                 {"Content-Type": "application/x-www-form-urlencoded"})
            if st != 200:
                raise RpsError(f"token request failed HTTP {st}: {raw[:200]!r}")
            d = json.loads(raw)
            self._token = d["access_token"]
            self._exp = now + int(d.get("expires_in", 1800))
            return self._token

    def _transform(self, action, role, instances, logging_attrs=None):
        # context/request guids must be valid UUIDs (engine validates them as System.Guid)
        evidences = [{"name": "Role", "value": role}, {"name": "Action", "value": action}]
        rg, pg, qg = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        payload = {
            "rightsContexts": [{"guid": rg, "evidences": evidences}],
            "processingContexts": [{"guid": pg, "evidences": evidences}],
            "requests": [{"guid": qg, "rightsContext": rg, "processingContext": pg,
                          "instances": instances}],
        }
        # loggingContext uses `evidences`, and the engine rejects any with an empty name/value
        attrs = [a for a in (logging_attrs or []) if a.get("name") and a.get("value")]
        if attrs:
            payload["loggingContext"] = {"evidences": attrs}

        def call(token):
            return self._post(
                f"{self.engine_url}/{self.transform_path}", json.dumps(payload).encode(),
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

        token = self._get_token()
        st, raw = call(token)
        if st == 401:
            st, raw = call(self._get_token(force=True))
        out = json.loads(raw) if raw else {}
        if st >= 400 or out.get("error"):
            err = out.get("error") or {}
            raise RpsError(f"transform failed HTTP {st}: {err or raw[:200]!r}",
                           code=(err.get("code") if isinstance(err, dict) else None))
        insts = []
        for resp in out.get("responses", []):
            insts.extend(resp.get("instances", []))
        return insts

    # --- public helpers (single value) ---
    def protect(self, value, property_name, role="R_MANAGER", class_name=None, logging_attrs=None):
        insts = self._transform("Protect", role,
                                [{"className": class_name or self.default_class,
                                  "propertyName": property_name, "value": value}], logging_attrs)
        return self._one(insts)

    def unprotect(self, value, property_name, role="R_MANAGER", class_name=None, logging_attrs=None):
        insts = self._transform("Unprotect", role,
                                [{"className": class_name or self.default_class,
                                  "propertyName": property_name, "value": value}], logging_attrs)
        return self._one(insts)

    # --- batch helpers (one transform call for many values — used for word-by-word content) ---
    def protect_many(self, values, property_name, role, class_name=None, logging_attrs=None):
        return self._many("Protect", values, property_name, role, class_name, logging_attrs)

    def unprotect_many(self, values, property_name, role, class_name=None, logging_attrs=None):
        return self._many("Unprotect", values, property_name, role, class_name, logging_attrs)

    def search_bands(self, values, property_name, role, class_name=None):
        """PROPE EqualSearch (Search action): per value, the order-preserving [min,max] band that
        brackets every (non-deterministic) stored token for it. The band is in each response
        instance's `dependencyContext`, not its `value`. Returns [(min, max), …]."""
        if not values:
            return []
        cn = class_name or self.default_class
        insts = self._transform("Search", role,
                                [{"className": cn, "propertyName": property_name, "value": v} for v in values])
        out = []
        for i in insts:
            if i.get("error"):
                raise RpsError(f"instance error: {i['error']}", code=i["error"].get("code"))
            ev = {e["name"]: e["value"] for e in (i.get("dependencyContext") or {}).get("evidences", [])}
            out.append((ev.get("min"), ev.get("max")))
        return out

    def _many(self, action, values, property_name, role, class_name, logging_attrs):
        if not values:
            return []
        cn = class_name or self.default_class
        insts = self._transform(action, role,
                                 [{"className": cn, "propertyName": property_name, "value": v} for v in values],
                                 logging_attrs)
        if len(insts) != len(values):
            raise RpsError(f"transform returned {len(insts)} instances for {len(values)} values")
        out = []
        for i in insts:
            if i.get("error"):
                raise RpsError(f"instance error: {i['error']}", code=i["error"].get("code"))
            out.append(i.get("value"))
        return out

    @staticmethod
    def _one(insts):
        if not insts:
            raise RpsError("no instance in transform response")
        i = insts[0]
        if i.get("error"):
            raise RpsError(f"instance error: {i['error']}", code=i["error"].get("code"))
        return i.get("value")
