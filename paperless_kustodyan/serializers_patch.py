"""
Make paperless's NATIVE API role-aware: transparently deprotect protected `content`, `title`,
tag `name`, correspondent `name` and custom-field `value` for the requesting user, decided by
their RPS role. Logging in and browsing paperless then renders everything per role (full /
masked / token / denied) without any special endpoint.

We monkeypatch each serializer's to_representation from AppConfig.ready() — paperless exposes no
hook for this, and we don't edit its source. Deprotect runs per-request, so the same stored
ciphertext yields different output per caller.

Stored layouts (`kproto:` prefix stripped) — read the FIRST token, the search tokens follow it:
  content              : "<full-copy> <det-word>…"    → first token = probabilistic full copy
  title, correspondent : "<enc-blob> <prope-word>…"   → first token = encode_blob'd full copy (PROPE search)
  custom-field         : "<token>"                    → one probabilistic full-fidelity token (no search)
  tag                  : "<det-word>…"                → decode + unprotect each deterministic word token
"""
import logging

from . import config

log = logging.getLogger("paperless_kustodyan")

# pure-blob kinds (no search tokens) → the RPS property to deprotect with
_BLOB = {
    "customfield": lambda: config.CUSTOMFIELD_PROPERTY,
}


def deprotect(value, kind, role):
    """kind ∈ {content, title, correspondent, customfield, tag}. Render `value` for `role`."""
    if not isinstance(value, str) or not config.is_protected(value):
        return value
    if not role:
        return value  # no role → leave the protected blob
    body = value[len(config.PROTECT_PREFIX):]
    c = config.client()
    try:
        # hybrid fields: display blob is the FIRST token, search tokens follow (read the blob)
        if kind == "content" and config.CONTENT_READ_PROPERTY:
            return c.unprotect(body.split(" ", 1)[0], config.CONTENT_READ_PROPERTY, role=role) if body else ""
        if kind == "title":
            # hybrid: first token is the encode_blob'd display copy, PROPE search tokens follow
            blob = config.decode_blob(body.split(" ", 1)[0]) if config.TITLE_SEARCH_PROPERTY else body
            return c.unprotect(blob, config.TITLE_PROPERTY, role=role) if blob else ""
        if kind == "correspondent":
            blob = config.decode_blob(body.split(" ", 1)[0]) if config.CORRESPONDENT_SEARCH_PROPERTY else body
            return c.unprotect(blob, config.CORRESPONDENT_PROPERTY, role=role) if blob else ""
        if kind in _BLOB:
            return c.unprotect(body, _BLOB[kind](), role=role) if body else ""
        # tag / content-without-a-full-copy: word-token stream when searchable, else a single blob
        prop = config.TAG_PROPERTY if kind == "tag" else config.CONTENT_PROPERTY
        if not config.SEARCHABLE_CONTENT:
            return c.unprotect(body, prop, role=role) if body else ""
        words = body.split()
        return " ".join(c.unprotect_many([config.decode_token(w) for w in words], prop, role=role)) if words else ""
    except Exception as e:  # never break serialization — fall back to the stored value
        log.warning("kustodyan: serializer deprotect failed (%s): %s", kind, e)
        return value


def _role(self):
    req = self.context.get("request")
    user = getattr(req, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return config.role_for_user(user)
    return None


def _patch(cls, field_kinds):
    orig = cls.to_representation
    if getattr(orig, "_kustodyan_patched", False):
        return
    def to_representation(self, instance):
        data = orig(self, instance)
        if not isinstance(data, dict):
            return data
        role = _role(self)
        for key, kind in field_kinds.items():
            if key in data:
                data[key] = deprotect(data[key], kind, role)
        return data
    to_representation._kustodyan_patched = True
    cls.to_representation = to_representation


def install():
    from documents import serialisers as S

    doc_fields = {"content": "content"}
    if config.PROTECT_TITLE:
        doc_fields["title"] = "title"
    _patch(S.DocumentSerializer, doc_fields)
    if hasattr(S, "DocumentListSerializer"):
        _patch(S.DocumentListSerializer, doc_fields)
    _patch(S.TagSerializer, {"name": "tag"})
    if config.PROTECT_CORRESPONDENTS and hasattr(S, "CorrespondentSerializer"):
        _patch(S.CorrespondentSerializer, {"name": "correspondent"})
    if (config.PROTECT_CUSTOM_FIELDS or config.PROTECTED_CUSTOM_FIELDS) and hasattr(S, "CustomFieldInstanceSerializer"):
        _patch(S.CustomFieldInstanceSerializer, {"value": "customfield"})
    log.info("kustodyan: patched serializers for role-aware deprotect-on-read")
