"""
Make paperless full-text search work over the encrypted, tokenized index.

On paperless-ngx 2.x (Whoosh): the index holds tokens, not words, so we intercept
`DelayedFullTextQuery._get_query` and rewrite each query term the SAME way the data was
protected, before paperless parses it:
  - content              → deterministic token (`k`+hex) → exact term match (equality only)
  - title, correspondent → PROPE EqualSearch [min,max] band → `field:[lo TO hi]` range query;
    a full word gives equality, a `word*` prefix gives begins-with — one call serves both.

On paperless-ngx 3.x (Tantivy): the Whoosh backend is gone. Content is still a
deterministic token, so an exact `term_query` reproduces equality. Title/correspondent
PROPE bands cannot be a native text range (Tantivy refuses those), so a band is
decomposed into a union of lexicographic-range globs (`prefix + [c1-c2] + ?…`, one glob per
bucket of the fixed-width token space) and matched with `regex_query` (simple modes) or
`Wildcard` grammar terms (advanced QUERY mode). Tantivy's `regex_query` full-matches a term,
and the `k`+hex tokens are the only tokens that can match a `k[0-9a-f]…` pattern (display
blobs start with `h`, the marker with `kproto`), so the union matches exactly the tokens in
the band.

Search modes (intercepted at `TantivyBackend._parse_query`, the single funnel):
  - TEXT / TITLE (simple bar) → built directly as tantivy.Query (title/content equality)
  - QUERY (advanced grammar)  → string-rewrite of bare/content:/title:/correspondent: terms,
                                 then the stock whoosh-compat parser runs over the rewritten string

Everything not searchable (dates, numbers, other fields, quoted phrases) is left untouched
and fails closed to an empty result — a miss, never a leak.
No paperless source is edited.
"""
import logging
import re

from . import config

log = logging.getLogger("paperless_kustodyan")

_OPERATORS = {"AND", "OR", "NOT", "TO", "NEAR"}
_HEX = "0123456789abcdef"
# The three fields whose values are protected into ciphertext and therefore searchable.
_SEARCH_FIELDS = {"content", "title", "correspondent"}

# Whoosh-era tokenizer (2.x): a word optionally followed by '*' (begins-with).
_TERM_RE = re.compile(r"(\w+)(\*?)", re.UNICODE)
# Tantivy QUERY-mode rewrite: a searchable-field term, OR a bare word that is not part of a
# `field:value` pair (not preceded by `:`/word-char, not followed by `:`), OR a quoted string
# (copied verbatim so a phrase is never torn into single words).
_ADV_RE = re.compile(
    r'(?P<field>content|title|correspondent):(?P<fval>\w+)(?P<fstar>\*?)'
    r'|'
    r'(?<![:\w])(?P<word>\w++)(?P<wstar>\*?)(?!\s*:)'
    r'|'
    r'(?P<quote>"(?:[^"\\]|\\.)*")',
)


class _NoCorrection:
    """Stand-in for whoosh's Correction so paperless's `corrected.string != q_str`
    check is False and the suggestion branch is skipped without raising."""
    __slots__ = ("query", "string")

    def __init__(self, query, string):
        self.query = query
        self.string = string


# --- lexicographic range → union of globs (PROPE band) -----------------------

def _lo_edge(prefix, rest, width, out):
    """Globs for `prefix` + (width chars) that are >= `rest` (width chars)."""
    if width == 0:
        out.append(prefix)
        return
    first = rest[0]
    idx = _HEX.index(first)
    if idx + 1 < 16:
        out.append(prefix + "[" + _HEX[idx + 1] + "-f]" + "?" * (width - 1))
    _lo_edge(prefix + first, rest[1:], width - 1, out)


def _hi_edge(prefix, rest, width, out):
    """Globs for `prefix` + (width chars) that are <= `rest` (width chars)."""
    if width == 0:
        out.append(prefix)
        return
    first = rest[0]
    idx = _HEX.index(first)
    if idx > 0:
        out.append(prefix + "[0-" + _HEX[idx - 1] + "]" + "?" * (width - 1))
    _hi_edge(prefix + first, rest[1:], width - 1, out)


def _range_globs(lo, hi):
    """Globs whose union == {s : lo <= s <= hi} over the hex alphabet (equal length)."""
    if len(lo) != len(hi):
        return None
    if lo == hi:
        return [lo]
    n = len(lo)
    i = 0
    while i < n and lo[i] == hi[i]:
        i += 1
    prefix = lo[:i]
    out = []
    li, hii = _HEX.index(lo[i]), _HEX.index(hi[i])
    if hii - li > 1:
        out.append(prefix + "[" + _HEX[li + 1] + "-" + _HEX[hii - 1] + "]" + "?" * (n - i - 1))
    _lo_edge(prefix + lo[i], lo[i + 1 :], n - i - 1, out)
    _hi_edge(prefix + hi[i], hi[i + 1 :], n - i - 1, out)
    return out


def _band_globs(lo, hi):
    """PROPE EqualSearch [lo, hi] → glob patterns over the encoded `k`+hex search tokens.

    encode_search lowercases before hex so the globs reproduce the engine's
    case-insensitive collation (see config.encode_search). Returns None if the band is
    unusable (missing/odd-length), so the caller can fail the term closed.
    """
    if not lo or not hi:
        return None
    enc_lo = config.encode_search(lo)
    enc_hi = config.encode_search(hi)
    globs = _range_globs(enc_lo[1:], enc_hi[1:])
    if globs is None:
        return None
    return ["k" + g for g in globs]


def _glob_regex(glob):
    """One glob → a full-match regex (`?` → `.`; `[...]` stays a char class)."""
    return glob.replace("?", ".")


# --- content equality token --------------------------------------------------

def _content_token(word):
    """Deterministic content search token for a lowercased word (write-side mirror)."""
    tok = config.client().protect(
        word + config.SEARCH_SALT,
        config.CONTENT_PROPERTY,
        role=config.PROTECT_ROLE,
    )
    return config.encode_token(tok)


# --- simple (TEXT/TITLE) modes: direct tantivy.Query -------------------------

def _build_simple_query(index, raw_query, include_content):
    """Direct ciphertext query for the simple search bar / title filter.

    Tokenizes with the SAME regex the write side uses (config.WORD_RE over lowercased text)
    so a query word maps to the exact stored token/band. ANDs across words, ORs content
    (equality) with title (PROPE band), exactly like the stock simple search but over tokens.
    Returns None when there is nothing searchable configured, so the caller falls through.
    """
    words = list(dict.fromkeys(config.WORD_RE.findall(raw_query.lower())))
    if not words:
        return None

    content_on = include_content and config.PROTECT_DOCUMENT_CONTENT
    title_on = config.PROTECT_TITLE and config.TITLE_SEARCH_PROPERTY
    if not content_on and not title_on:
        return None

    # One engine call per property, batched over the whole query.
    content_tokens = {}
    if content_on:
        salted = [w + config.SEARCH_SALT for w in words]
        toks = config.client().protect_many(salted, config.CONTENT_PROPERTY, role=config.PROTECT_ROLE)
        content_tokens = dict(zip(words, (config.encode_token(t) for t in toks)))

    title_globs = {}
    if title_on:
        bands = config.client().search_bands(words, config.TITLE_SEARCH_PROPERTY, role=config.PROTECT_ROLE)
        for w, band in zip(words, bands):
            g = _band_globs(*band) if band else None
            if g:
                title_globs[w] = g

    import tantivy

    word_clauses = []
    for w in words:
        clauses = []
        if w in content_tokens:
            clauses.append((tantivy.Occur.Should,
                            tantivy.Query.term_query(index.schema, "simple_content", content_tokens[w])))
        if w in title_globs:
            title_queries = [tantivy.Query.regex_query(index.schema, "simple_title", _glob_regex(g))
                             for g in title_globs[w]]
            clauses.append((tantivy.Occur.Should,
                            _any_of([(tantivy.Occur.Should, q) for q in title_queries])))
        if clauses:
            word_clauses.append((tantivy.Occur.Must, _any_of(clauses)))

    if not word_clauses:
        return None
    return _any_of(word_clauses)


def _any_of(clauses):
    """Collapse a clause list: none → empty, one → itself, many → boolean_query."""
    import tantivy

    if not clauses:
        return tantivy.Query.empty_query()
    if len(clauses) == 1:
        return clauses[0][1]
    return tantivy.Query.boolean_query(clauses)


# --- advanced QUERY mode: string rewrite -------------------------------------

def _band_clause(field, word):
    """`(field:glob1 OR field:glob2 …)` for a PROPE field, or None if the band fails."""
    try:
        bands = config.client().search_bands([word], _SEARCH_PROPERTY[field], role=config.PROTECT_ROLE)
    except Exception as e:
        log.warning("kustodyan: search_bands(%s) failed: %s", field, e)
        return None
    band = bands[0] if bands else None
    if not band:
        return None
    globs = _band_globs(*band)
    if not globs:
        return None
    # whoosh-compat grammar rejects a terminal `[…]` (no wildcard after it). Tokens are
    # fixed-width, so a terminal glob `…[c1-c2]` can carry a trailing `*` that only ever
    # matches the empty tail — semantically identical, grammatically valid.
    terms = [f"{field}:{g}*" if g.endswith("]") else f"{field}:{g}" for g in globs]
    if len(terms) == 1:
        return terms[0]
    return "(" + " OR ".join(terms) + ")"


def _rewrite_advanced_query(q_str):
    """Rewrite bare/content:/title:/correspondent: terms of an advanced query to ciphertext.

    Runs against the LIVE engine per searchable term (same as the 2.x hook). Anything the
    rewrite does not recognise — operators, other fields, numbers, dates, quoted phrases —
    is returned verbatim, so the stock parser treats it as before (a miss over ciphertext).
    """
    def repl(m):
        if m.group("quote"):
            return m.group("quote")
        if m.group("field") is not None:
            field = m.group("field")
            word = m.group("fval").lower()
            if field == "content":
                if m.group("fstar"):
                    return m.group(0)  # no content prefix search — leave verbatim (miss)
                try:
                    return f"content:{_content_token(word)}"
                except Exception as e:
                    log.warning("kustodyan: content protect(%s) failed: %s", word, e)
                    return m.group(0)
            clause = _band_clause(field, word)
            return clause if clause else m.group(0)
        # bare word
        word = m.group("word")
        upper = word.upper()
        if upper in _OPERATORS or word.isdigit():
            return m.group(0)
        w = word.lower()
        # Keep the original term too: the stock parser searches the whole default-field
        # set (title/content/correspondent/document_type/tag), and the plaintext
        # document_type/tag are not covered by the ciphertext clauses below. Over the
        # encrypted fields the literal word matches nothing, so it only ever adds the
        # plaintext matches the rewrite would otherwise drop.
        parts = [m.group(0)]
        try:
            if config.PROTECT_DOCUMENT_CONTENT and not m.group("wstar"):
                parts.append(f"content:{_content_token(w)}")
        except Exception as e:
            log.warning("kustodyan: content protect(%s) failed: %s", w, e)
        if config.PROTECT_TITLE and config.TITLE_SEARCH_PROPERTY:
            c = _band_clause("title", w)
            if c:
                parts.append(c)
        if config.PROTECT_CORRESPONDENTS and config.CORRESPONDENT_SEARCH_PROPERTY:
            c = _band_clause("correspondent", w)
            if c:
                parts.append(c)
        if len(parts) == 1:
            return m.group(0)
        return "(" + " OR ".join(parts) + ")"

    return _ADV_RE.sub(repl, q_str)


_SEARCH_PROPERTY = {
    "title": config.TITLE_SEARCH_PROPERTY,
    "correspondent": config.CORRESPONDENT_SEARCH_PROPERTY,
}


# --- Whoosh (2.x) hook, unchanged -------------------------------------------

def _tokenize_query(q_str: str) -> str:
    def repl(m):
        word, star = m.group(1), m.group(2)
        if word.upper() in _OPERATORS:
            return word + star
        try:
            c = config.client()
            w = word.lower()
            parts = []
            for sp, field in ((config.TITLE_SEARCH_PROPERTY, "title"), (config.CORRESPONDENT_SEARCH_PROPERTY, "correspondent")):
                if not sp:
                    continue
                bands = c.search_bands([w], sp, role=config.PROTECT_ROLE)
                lo, hi = bands[0] if bands else (None, None)
                if lo and hi:
                    parts.append(f"{field}:[{config.encode_search(lo)} TO {config.encode_search(hi)}]")
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


def _install_whoosh():
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
        searcher = getattr(self, "searcher", None)
        patched_searcher = searcher is not None and "correct_query" not in searcher.__dict__
        try:
            self.query_params = dict(saved)
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


def _install_tantivy():
    from documents.search import _backend
    from documents.search._backend import SearchMode

    cls = _backend.TantivyBackend
    orig = getattr(cls, "_parse_query", None)
    if orig is None or getattr(orig, "_kustodyan_patched", False):
        return

    def _parse_query(self, query, search_mode):
        if not (config.SEARCHABLE_CONTENT and query and query.strip()):
            return orig(self, query, search_mode)
        if search_mode is SearchMode.TEXT:
            built = _build_simple_query(self._index, query, include_content=True)
            return built if built is not None else orig(self, query, search_mode)
        if search_mode is SearchMode.TITLE:
            built = _build_simple_query(self._index, query, include_content=False)
            return built if built is not None else orig(self, query, search_mode)
        # QUERY mode: rewrite searchable terms, then let the stock parser run.
        rewritten = _rewrite_advanced_query(query)
        if rewritten != query:
            return orig(self, rewritten, search_mode)
        return orig(self, query, search_mode)

    _parse_query._kustodyan_patched = True
    cls._parse_query = _parse_query
    log.info("kustodyan: patched TantivyBackend._parse_query for tokenized search")


def install():
    if not config.SEARCHABLE_CONTENT:
        return
    try:
        from documents import index as I  # noqa: F401
    except ImportError:
        # paperless-ngx 3.x: Whoosh removed. Use the Tantivy hook.
        try:
            _install_tantivy()
        except Exception as e:
            log.warning("kustodyan: Tantivy search patch failed: %s", e)
        return
    _install_whoosh()
