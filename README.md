# paperless-kustodyan

A [paperless-ngx](https://docs.paperless-ngx.com) add-on that puts the **Kustodyan** data-protection
API (RegData Protection Suite / RPS) in front of your documents: sensitive fields are **encrypted at
rest *and* in the search index**, **searchable without decrypting** (full-word *and* begins-with), and
**revealed differently per user** — all decided server-side by the RPS configuration.

It is a standalone Django app (`paperless_kustodyan`) baked into a custom image and activated via
`PAPERLESS_APPS`. **No paperless source files are modified.**

## What it does

- **Protect on write** — `pre_save` receivers tokenize each configured field through the Kustodyan
  Engine before it is persisted; cleartext never reaches the database (fail-closed: a transform error
  aborts the save). Each field uses the scheme that fits it:

  | Field | Scheme | Searchable | Notes |
  |---|---|---|---|
  | content | deterministic + probabilistic full copy | equality | keeps paperless's ML auto-classifier working |
  | title, correspondent | **PROPE** (probabilistic) + full copy | equality + begins-with | non-deterministic tokens hide word frequency |
  | tag | deterministic | equality | |
  | custom field | probabilistic | — | high-secrecy, not searched |

- **Search through the encrypted index** — a patch on paperless's full-text query rewrites each term
  the same way the data was protected (deterministic token for content; a PROPE order-preserving range
  for title/correspondent), so `salary`, `emp*`, `acme`… match without anything being decrypted.
- **Reveal on read, per role** — paperless's native serializers are patched so every value is
  deprotected *for the requesting user's role*: full cleartext / masked / stored-token / denied. The
  document preview is likewise rendered on the fly, in the document's native type (image/text/PDF),
  with `Cache-Control: no-store`.
- **Auto-classification still works** — the classifier trains on the deterministic content tokens, so
  paperless keeps predicting correspondents/tags over encrypted data.

Role mapping (Django group → RPS role) is configurable; the reference config uses
`DATA_STEWARD` (full) / `CASE_WORKER` (masked) / `STAFF` (stored token) / none → denied.

## Configuration (env)

| Variable | Purpose |
|---|---|
| `KUSTODYAN_IDENTITY_URL`, `KUSTODYAN_ENGINE_URL` | RPS API base URLs |
| `KUSTODYAN_CLIENT_ID`, `KUSTODYAN_CLIENT_SECRET` | Engine API client |
| `KUSTODYAN_DEFAULT_CLASSNAME` | RPS class for the data instances |
| `KUSTODYAN_PROTECT_DOCUMENT_CONTENT` / `_TITLE` / `_CORRESPONDENTS` / `_CUSTOM_FIELDS` | what to protect |
| `KUSTODYAN_*_PROPERTY` (`CONTENT`/`CONTENT_READ`/`TITLE`/`TITLE_SEARCH`/`CORRESPONDENT`/`CORRESPONDENT_SEARCH`/`TAG`/`CUSTOMFIELD`) | RPS property per field |
| `KUSTODYAN_SENSITIVE_TAGS` | comma-separated tag names to protect |
| `KUSTODYAN_SEARCH_SALT` | app-held suffix salt on content tokens (separate from the engine key) |
| `KUSTODYAN_{STEWARD,WORKER,STAFF}_GROUP` / `_ROLE` | Django group → RPS role mapping |

See `paperless_kustodyan/config.py` for the full set and defaults.

## Setup

1. **Provision an RPS configuration** with the properties/roles above (a builder is in the companion
   `kustodyan-mcp` repo, `provisioning/ope_provision.py`).
2. **Build the image** (`Dockerfile`) — paperless-ngx + this add-on.
3. **Run** with the env above and `PAPERLESS_APPS=paperless_kustodyan.apps.PaperlessKustodyanConfig`.
4. **Try it** — `python manage.py shell < demo/showcase_seed.py`, then search `salary` / `emp*` /
   `acme` and open a document as different roles. `demo/render_showcase.py` prints the per-role views.

## Design notes
- `signals.py` protect-on-write · `serializers_patch.py` + `preview_patch.py` role-aware reveal ·
  `search_patch.py` query rewrite over the encrypted index · `classifier_patch.py` ML over tokens ·
  `rps_client.py` a stdlib-only Engine client.
- PROPE hides word frequency (non-deterministic tokens) but is order-preserving; deterministic content
  keeps the classifier working. Searchability is a leakage trade-off — protect probabilistically any
  field you don't need to search.

## License
MIT — see [`LICENSE`](LICENSE).
