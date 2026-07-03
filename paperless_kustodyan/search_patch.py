"""
Make paperless full-text search work over the encrypted, tokenized index.

The Whoosh index holds tokens, not words, so we intercept `DelayedFullTextQuery._get_query`
and rewrite each query term the SAME way the data was protected, before paperless parses it:
  - content              → deterministic token (`k`+hex) → exact term match (equality only)
  - title, correspondent → PROPE EqualSearch [min,max] band → `field:[lo TO hi]` range query;
    a full word gives equality, a `word*` prefix gives begins-with — one call serves both.
No paperless source is edited.
"""
import logging
import re

from . import config

log = logging.getLogger("paperless_kustodyan")
_OPERATORS = {"AND", "OR", "NOT", "TO", "NEAR"}
_TERM_RE = re.compile(r"(\w+)(\*?)", re.UNICODE)   # a word, optionally followed by '*' (begins-with)


class _NoCorrection:
    """Stand-in for whoosh's Correction so paperless's `corrected.string != q_str`
    check is False and the suggestion branch is skipped without raising."""
    __slots__ = ("query", "string")

    def __init__(self, query, string):
        self.query = query
        self.string = string


def _tokenize_query(q_str: str) -> str:
    def repl(m):
        word, star = m.group(1), m.group(2)
        if word.upper() in _OPERATORS:
            return word + star
        try:
            c = config.client()
            w = word.lower()
            parts = []
            # PROPE fields (title, correspondent): EqualSearch band → range/BETWEEN — equality for a
            # full word, begins-with for a prefix; the band is prefix-granular so one call serves both.
            for sp, field in ((config.TITLE_SEARCH_PROPERTY, "title"), (config.CORRESPONDENT_SEARCH_PROPERTY, "correspondent")):
                if not sp:
                    continue
                bands = c.search_bands([w], sp, role=config.PROTECT_ROLE)
                lo, hi = bands[0] if bands else (None, None)
                if lo and hi:
                    parts.append(f"{field}:[{config.encode_search(lo)} TO {config.encode_search(hi)}]")
            # content (deterministic): equality only — skip for begins-with (no prefix search)
            if not star:
                det = config.encode_token(c.protect(w + config.SEARCH_SALT, config.CONTENT_PROPERTY, role=config.PROTECT_ROLE))
                parts.append(f"content:{det}")
            if not parts:
                return word + star
            return "(" + " OR ".join(parts) + ")" if len(parts) > 1 else parts[0]
        except Exception as e:
            log.warning("kustodyan: query tokenize failed: %s", e)
            return word + star
    return _TERM_RE.sub(repl, q_str)


def install():
    if not config.SEARCHABLE_CONTENT:
        return
    from documents import index as I

    cls = getattr(I, "DelayedFullTextQuery", None)
    if cls is None or not hasattr(cls, "_get_query"):
        log.warning("kustodyan: DelayedFullTextQuery._get_query not found; search patch skipped")
        return
    orig = cls._get_query
    if getattr(orig, "_kustodyan_patched", False):
        return

    def _get_query(self):
        q = self.query_params.get("query")
        if not q:
            return orig(self)
        saved = self.query_params
        # The rewritten query holds TermRange nodes (PROPE bands) and opaque hex tokens.
        # Whoosh's spell-corrector walks query leaves for `.text` and chokes on ranges
        # (logged INFO: "Error while correcting query ... 'TermRange' object has no
        # attribute 'text'"); correction is meaningless on tokens anyway. No-op it for
        # this query, then restore the searcher's bound method.
        searcher = getattr(self, "searcher", None)
        patched_searcher = searcher is not None and "correct_query" not in searcher.__dict__
        try:
            self.query_params = dict(saved)          # mutable copy (request.GET is an immutable QueryDict)
            self.query_params["query"] = _tokenize_query(q)
            if patched_searcher:
                searcher.correct_query = lambda query, qstring, *a, **k: _NoCorrection(query, qstring)
            return orig(self)
        finally:
            self.query_params = saved
            if patched_searcher:
                searcher.__dict__.pop("correct_query", None)

    _get_query._kustodyan_patched = True
    cls._get_query = _get_query
    log.info("kustodyan: patched DelayedFullTextQuery for tokenized search")
