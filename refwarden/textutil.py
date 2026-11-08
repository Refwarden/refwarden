"""Small text helpers shared by parsing, matching and the heuristic checks."""
import re
import unicodedata

STOPWORDS = set("""
a an the and or of in on at to for from by with without into onto over under than then that this these those
is are was were be been being has have had do does did not no nor but if as it its their there here which who whom
whose what when where why how we our us you your they them he she his her i me my can could may might must shall
should will would also such both either neither each every all any some more most other only same so very via per
between among within across about after before during while upon toward towards against using used use based study
studies paper article research results result findings finding evidence data analysis effect effects show shows shown
new et al vs versus e g i
""".split())

PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "del", "della", "la", "le", "du", "dos", "das", "ter", "ten", "bin", "al", "el"}

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})([a-z]?)\b")


def fold(s):
    """Lowercase and strip accents so 'García' matches 'Garcia'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = re.sub("[\u2010-\u2015\u2212]", "-", s).replace("\u2019", "'").replace("\u2018", "'")   # Crossref: 'Al‐Shaer', 'O’Neill'
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def stem(t):
    """Light suffix stripping so 'replications' meets 'replication' and 'studies' meets 'study'."""
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf) and not t.endswith("ss"):
            return t[:-len(suf)]
    return t


def tokens(s, min_len=3):
    return [stem(t) for t in re.findall(r"[a-z0-9]+", fold(s)) if len(t) >= min_len and t not in STOPWORDS]


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_NUMWORD = re.compile(r"\b(?:(" + "|".join(_TENS) + r")(?:[\s-](" + "|".join(k for k in _UNITS if k != "zero") + r"))?|("
                      + "|".join(_UNITS) + r"))\b(\s+(?:percent|per cent))?", re.I)


def spelled_numbers_to_digits(text):
    """'Thirty-six percent' -> '36%', 'ninety-seven' -> '97', so figures written in words can be matched."""
    def sub(m):
        n = _TENS[m.group(1).lower()] + (_UNITS[m.group(2).lower()] if m.group(2) else 0) if m.group(1) else _UNITS[m.group(3).lower()]
        return f"{n}%" if m.group(4) else str(n)
    return _NUMWORD.sub(sub, text or "")


def content_terms(s):
    return set(tokens(s))


def containment(needle, haystack):
    """Share of the needle's content terms that appear in the haystack (0..1)."""
    a = content_terms(needle)
    if not a:
        return 0.0
    return len(a & content_terms(haystack)) / len(a)


def jaccard(a, b):
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_doi(s):
    m = _DOI_RE.search(s or "")
    if not m:
        return None
    doi = m.group(1).rstrip(".,;:)]}>'\"")
    # a DOI wrapped in parentheses keeps a trailing ')' only if it also opened one
    return doi.lower()


def clean_doi(doi):
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    return doi.lower() or None


def find_year(s):
    m = _YEAR_RE.search(s or "")
    return (int(m.group(1)), m.group(2)) if m else (None, "")


def is_initials(tok):
    t = tok.strip(".,")
    return bool(re.fullmatch(r"(?:[A-Z]\.?-?){1,3}", t)) or (t.isupper() and len(t) <= 3)


def surname_key(chunk):
    """Normalise the first author of a reference or in-text citation to a match key.

    'Smith, J.' / 'J. Smith' / 'Smith JK' / 'van Dijk, T.' / 'García Márquez' -> last
    non-initial, non-particle token, folded.
    """
    chunk = re.split(r"\bet\s+al\b|&|\band\b|,|\(", chunk or "")[0]
    toks = [t for t in re.findall(r"[^\W\d_][\w'’\-]*\.?", chunk)]
    toks = [t for t in toks if not is_initials(t) and fold(t).strip(".") not in PARTICLES]
    if not toks:
        return None
    return re.sub(r"['’]s$", "", fold(toks[-1]).strip(".'’"))   # possessive: "Ajzen's (2011)"


# --- sentences ---------------------------------------------------------------

_ABBREV = ["et al.", "e.g.", "i.e.", "cf.", "Fig.", "Figs.", "fig.", "vs.", "pp.", "p.", "No.", "Vol.", "Eq.", "Ref.",
           "Refs.", "approx.", "ca.", "Dr.", "Prof.", "St.", "Jr.", "Inc.", "Ltd.", "Co.", "U.S.", "U.K.", "etc."]
_SENT_BOUNDARY = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+(?=[A-Z0-9\"“(\[])")


def sentence_spans(text):
    """Return (start, end) spans of sentences. Abbreviations are masked so 'et al.' does not split.

    Blank lines and heading lines are hard boundaries, so a heading never fuses with the next sentence.
    """
    masked = re.sub(r"\bet(\s+)al\.", lambda m: "et" + m.group(1) + "al\u2024", text)
    for ab in _ABBREV:
        masked = masked.replace(ab, ab[:-1] + "\u2024")
    # initials such as 'J. Smith' inside citations
    masked = re.sub(r"\b([A-Z])\.(?=\s?[A-Z][a-z])", "\\1\u2024", masked)
    blocks, start, pos = [], 0, 0
    for line in masked.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            blocks.append((start, pos))                  # close the running paragraph
            if stripped:
                blocks.append((pos, pos + len(line)))    # heading is its own block
            start = pos + len(line)
        pos += len(line)
    blocks.append((start, len(masked)))
    spans = []
    for b0, b1 in blocks:
        start = b0
        for m in _SENT_BOUNDARY.finditer(masked, b0, b1):
            spans.append((start, m.start()))
            start = m.end()
        spans.append((start, b1))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def sentence_at(text, spans, pos):
    for a, b in spans:
        if a <= pos < b:
            return text[a:b].strip()
    return ""


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", s).strip()


def squash(s):
    return re.sub(r"\s+", " ", s or "").strip()


def norm_for_quote(s):
    return re.sub(r"[^a-z0-9]+", " ", fold(s)).strip()


def quote_in(quote, source):
    """True when the quote appears verbatim in the source, ignoring case, accents and punctuation."""
    q = norm_for_quote(quote)
    return bool(q) and len(q) >= 12 and q in norm_for_quote(source)
