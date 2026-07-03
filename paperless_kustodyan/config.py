"""Runtime configuration for the Kustodyan paperless integration (all env-driven)."""
import binascii
import json
import os
import re

from .rps_client import RpsClient

# --- searchable mode ---
# When true, content (and tags) are protected WORD-BY-WORD with a deterministic transformer
# (AESDeterministicProtector): same word → same token → the encrypted index is searchable by
# equality. Tokens are hex-encoded so Whoosh's \w+ analyzer keeps each as a single term.
SEARCHABLE_CONTENT = os.environ.get("KUSTODYAN_SEARCHABLE_CONTENT", "false").lower() == "true"
WORD_RE = re.compile(r"\w+", re.UNICODE)
# Secret salt suffixed to content words before tokenization. Held by the app (separate from the
# engine keys), so reversing a deterministic content token needs BOTH the engine key AND this salt.
# Content tokens are write-only for search, so no read-side un-salting is needed.
SEARCH_SALT = os.environ.get("KUSTODYAN_SEARCH_SALT", "")


def encode_token(engine_token: str) -> str:
    """Engine token (e.g. DC_…==_) → an analyzer-safe alnum word (k + lowercase hex)."""
    return "k" + binascii.hexlify(engine_token.encode()).decode()


def decode_token(word: str) -> str:
    return binascii.unhexlify(word[1:]).decode()


# PROPE search tokens (title): the engine's order-preserving comparison is CASE-INSENSITIVE, so we
# lower-case before hex so a Whoosh range query reproduces the engine's collation. (k + lc hex)
def encode_search(engine_token: str) -> str:
    return "k" + binascii.hexlify(engine_token.lower().encode()).decode()


# Display blob for a PROPE field: a single analyzer-safe term that sorts BELOW the 'k…' search
# tokens ('h' < 'k'), so a title:[k.. TO k..] range query can never match the display copy.
def encode_blob(blob: str) -> str:
    return "h" + binascii.hexlify(blob.encode()).decode()


def decode_blob(word: str) -> str:
    return binascii.unhexlify(word[1:]).decode()

# Marker prepended to a stored value once protected (so we never re-protect on re-save and
# know what to deprotect on read). The engine's own wrap prefixes (DC_/DG_) sit inside this.
PROTECT_PREFIX = os.environ.get("KUSTODYAN_PROTECT_PREFIX", "kproto:")

# --- what to protect ---
# Protect the full Document.content (→ RPS property "content").
PROTECT_DOCUMENT_CONTENT = os.environ.get("KUSTODYAN_PROTECT_DOCUMENT_CONTENT", "true").lower() == "true"
CONTENT_PROPERTY = os.environ.get("KUSTODYAN_CONTENT_PROPERTY", "content")
# Full-fidelity display: a probabilistic copy of the WHOLE content stored as the first whitespace-token
# (preserves original case/punctuation on read); the remaining tokens are the lowercased search words.
# Empty → read from the (lowercased) search tokens instead.
CONTENT_READ_PROPERTY = os.environ.get("KUSTODYAN_CONTENT_READ_PROPERTY", "")
# Protect Document.title: full-fidelity display via TITLE_PROPERTY. When TITLE_SEARCH_PROPERTY is
# set, the title is ALSO searchable (equality + begins-with) via PROPE — stored as the display blob
# (encode_blob) + non-deterministic PROPE word tokens (encode_search); search is a range/BETWEEN over
# the EqualSearch band. PROPE hides title-word frequency (content stays deterministic for the ML
# classifier, which PROPE would break).
PROTECT_TITLE = os.environ.get("KUSTODYAN_PROTECT_TITLE", "false").lower() == "true"
TITLE_PROPERTY = os.environ.get("KUSTODYAN_TITLE_PROPERTY", "title_full")
TITLE_SEARCH_PROPERTY = os.environ.get("KUSTODYAN_TITLE_SEARCH_PROPERTY", "")
# Protect the names of these tags (→ RPS property "tag"). Comma-separated cleartext names.
SENSITIVE_TAGS = {t.strip() for t in os.environ.get("KUSTODYAN_SENSITIVE_TAGS", "").split(",") if t.strip()}
TAG_PROPERTY = os.environ.get("KUSTODYAN_TAG_PROPERTY", "tag")
# Protect correspondent names (full-fidelity, role-aware display). When CORRESPONDENT_SEARCH_PROPERTY
# is set, also PROPE-searchable (equality + begins-with), same hybrid shape as title.
PROTECT_CORRESPONDENTS = os.environ.get("KUSTODYAN_PROTECT_CORRESPONDENTS", "false").lower() == "true"
CORRESPONDENT_PROPERTY = os.environ.get("KUSTODYAN_CORRESPONDENT_PROPERTY", "correspondent")
CORRESPONDENT_SEARCH_PROPERTY = os.environ.get("KUSTODYAN_CORRESPONDENT_SEARCH_PROPERTY", "")
# Protect string/text custom-field values (full-fidelity, role-aware display).
PROTECT_CUSTOM_FIELDS = os.environ.get("KUSTODYAN_PROTECT_CUSTOM_FIELDS", "false").lower() == "true"
CUSTOMFIELD_PROPERTY = os.environ.get("KUSTODYAN_CUSTOMFIELD_PROPERTY", "customfield")
# Legacy: per-field-name → property map (still honoured if set).
PROTECTED_CUSTOM_FIELDS = json.loads(os.environ.get("KUSTODYAN_PROTECTED_FIELDS", "{}"))

# Role used to protect-on-write (must have CanTransform + Protect rights).
PROTECT_ROLE = os.environ.get("KUSTODYAN_PROTECT_ROLE", "DATA_STEWARD")
# Read-only role (sees stored tokens, cannot reveal) — used to gate the role-aware preview.
STAFF_ROLE = os.environ.get("KUSTODYAN_STAFF_ROLE", "STAFF")

# --- who may read, and as which RPS role ---
# Django group → RPS role. A caller in several groups gets the strongest (first match here).
ROLE_GROUPS = [
    (os.environ.get("KUSTODYAN_STEWARD_GROUP", "kustodyan-steward"), os.environ.get("KUSTODYAN_STEWARD_ROLE", "DATA_STEWARD")),
    (os.environ.get("KUSTODYAN_WORKER_GROUP", "kustodyan-worker"), os.environ.get("KUSTODYAN_WORKER_ROLE", "CASE_WORKER")),
    (os.environ.get("KUSTODYAN_STAFF_GROUP", "kustodyan-staff"), os.environ.get("KUSTODYAN_STAFF_ROLE", "STAFF")),
]


def role_for_user(user):
    """Return the RPS role for a Django user, or None if they may not read protected data."""
    if user.is_superuser:
        return ROLE_GROUPS[0][1]  # superuser → strongest role
    names = set(user.groups.values_list("name", flat=True))
    for group, role in ROLE_GROUPS:
        if group in names:
            return role
    return None


_client = None


def client() -> RpsClient:
    global _client
    if _client is None:
        _client = RpsClient()
    return _client


def is_protected(value) -> bool:
    return isinstance(value, str) and value.startswith(PROTECT_PREFIX)


def protect_words(words, prop, role=None):
    """Protect many words via `prop` in ONE Engine call, de-duplicating within the call.

    Deliberately keeps NO cross-request cache: a persistent word→token map in the app's memory
    would be both a cleartext vocabulary of every document AND a ready-made rainbow table that
    reverses the encrypted index without the key. Per-call dedup gives the batching win without
    retaining cleartext.
    """
    role = role or PROTECT_ROLE
    uniq = list(dict.fromkeys(words))
    if not uniq:
        return []
    tokens = dict(zip(uniq, client().protect_many(uniq, prop, role=role)))
    return [tokens[w] for w in words]
