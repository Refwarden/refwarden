"""Manuscript intake: text extraction, reference-list split, in-text citation detection.

Supported: .txt .md .docx (stdlib), .bib (reference list only), .pdf (only if `pypdf` is installed).
Styles: numeric [1], [2-4], [1, 3]; author-year (Smith, 2020; Lee & Park, 2019a), Smith et al. (2020).
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from .textutil import find_doi, find_year, sentence_spans, surname_key, squash

REF_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\d+\.?\s*)?(references|bibliography|works cited|literature cited|reference list|"
    r"literatura|references and notes|sources)\s*:?\s*$", re.I | re.M)
END_OF_REFS = re.compile(r"^\s*(?:#{1,6}\s*)?(appendix|appendices|supplementary|tables?|figures?|figure legends|"
                         r"acknowledg|author contributions|funding|declarations?)\b", re.I)
NUM_PREFIX = re.compile(r"^\s*(?:\[(\d{1,4})\]|(\d{1,4})[.)])\s+")


# --- documents -----------------------------------------------------------------

def load_text(data, filename=""):
    name = filename.lower()
    if name.endswith(".docx"):
        return docx_text(data)
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader  # optional dependency
        except ImportError as e:
            raise ValueError("PDF input needs `pip install pypdf`; or export the manuscript to .docx/.txt") from e
        reader = PdfReader(io.BytesIO(data))
        return clean_pdf_text("\n".join(p.extract_text() or "" for p in reader.pages))
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


def clean_pdf_text(text):
    """Undo typical publisher-PDF extraction artefacts: running headers and footers, page numbers,
    and the spaces that layout inserts after hyphens ('Self-  Efficacy')."""
    from collections import Counter
    lines = text.splitlines()
    norm = lambda l: re.sub(r"^\s*\d{1,3}\s*|\s*\d{1,3}\s*$", "", l.strip())
    counts = Counter(norm(l) for l in lines if len(norm(l)) >= 15)
    repeated = {k for k, v in counts.items() if v >= 3}
    kept = []
    for l in lines:
        n = norm(l)
        if n in repeated or re.fullmatch(r"\s*\d{1,3}\s*", l) or re.match(r"\s*©\s*\d{4}\b", l):
            continue
        for h in repeated:   # a header glued to the first line of a page: '11 Journal Name … 2026 Smith, J., …'
            if l.strip() and h in l and l.strip() != h:
                l = re.sub(r"^\s*\d{0,3}\s*" + re.escape(h) + r"\s*", "", l)
        kept.append(l)
    out = re.sub(r"[\u00a0\u2000-\u200a\u202f]", " ", "\n".join(kept))
    # publisher section heads such as '3.4 | Statistical Analysis' become headings
    out = re.sub(r"(?m)^\s*(\d+(?:\.\d+)*)\s*\|\s*([A-Z][^\n]{1,90})$", r"# \1 \2", out)
    # words split at a line end are extracted as 'dig - ital', 'tangi - ble'
    out = re.sub(r"\b([a-z]{2,}) -\s*\n?\s*([a-z]{2,})\b", r"\1\2", out)
    out = re.sub(r"(\w)-[ \t]*\n[ \t]*([A-Z])", r"\1-\2", out)   # compound name broken at a line end: 'Haski-\nLeventhal'
    return re.sub(r"(\w)- +(\w)", r"\1-\2", out)


def docx_text(data):
    """Paragraph text of a .docx. Text-box paragraphs are read once: Word stores each text box twice
    (DrawingML plus a VML fallback), and nests it inside its anchor paragraph."""
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    fallback = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    paras = []

    def para(p):
        parts, nested = [], []

        def collect(el):
            for ch in el:
                if ch.tag == fallback:
                    continue
                if ch.tag == w + "p":
                    nested.append(ch)
                elif ch.tag in (w + "t", w + "tab"):
                    parts.append("\t" if ch.tag == w + "tab" else (ch.text or ""))
                else:
                    collect(ch)
        collect(p)
        style = p.find(f"{w}pPr/{w}pStyle")
        txt = "".join(parts)
        if style is not None and (style.get(w + "val") or "").lower().startswith("heading") and txt.strip():
            txt = "# " + txt
        paras.append(txt)
        for n in nested:
            para(n)

    def walk(el):
        for ch in el:
            if ch.tag == fallback:
                continue
            if ch.tag == w + "p":
                para(ch)
            else:
                walk(ch)
    walk(root)
    return "\n\n".join(paras)


# --- reference list -------------------------------------------------------------

def split_references(text):
    """Return (body, [raw reference strings])."""
    matches = list(REF_HEADING.finditer(text))
    if not matches:
        return text, []
    m = matches[-1]
    body, tail = text[:m.start()], text[m.end():]
    lines = tail.splitlines()
    for i, ln in enumerate(lines):
        if END_OF_REFS.match(ln) and len(ln) < 60:
            lines = lines[:i]
            break
    return body, split_entries(lines)


def split_entries(lines):
    nonblank = [l for l in lines if l.strip()]
    if not nonblank:
        return []
    numbered = sum(1 for l in nonblank if NUM_PREFIX.match(l))
    entries = []
    if numbered >= max(2, len(nonblank) // 3):
        for l in lines:
            if NUM_PREFIX.match(l):
                entries.append(l.strip())
            elif l.strip() and entries:
                entries[-1] += " " + l.strip()
        return [squash(e) for e in entries]
    blocks = [b for b in re.split(r"\n\s*\n", "\n".join(lines)) if b.strip()]
    author_starts = sum(1 for l in nonblank if AUTHOR_START.match(l.strip()))
    # wrapped lines with no blank line between entries (publisher PDFs): one entry per 'Surname, I.' start
    by_author = author_starts >= max(3, len(nonblank) // 6) and author_starts > 1.5 * len(blocks)
    multi_line_blocks = sum(1 for b in blocks if len(b.strip().splitlines()) > 1)
    if not by_author and len(blocks) > 1 and multi_line_blocks:
        return [squash(b) for b in blocks]
    for l in lines:
        s = l.strip()
        if not s:
            continue
        s = re.sub(r"^[-*•]\s+", "", s)
        continuation = s[0].islower() or s.lower().startswith(("doi", "http", "pp.", "retrieved"))
        if by_author and entries:
            # a new entry starts with 'Surname, I.' once the current one has its year
            continuation = not (AUTHOR_START.match(s) and YEAR_TOKEN.search(entries[-1])
                                and entries[-1].rstrip().endswith((".", ")")))
        if entries and continuation:
            entries[-1] += " " + s
        else:
            entries.append(s)
    return [squash(e) for e in entries]


AUTHOR_START = re.compile(r"^(?:(?:van|von|de|der|den|da|di|la|le|du|al|el)[\s-]+)?[A-Z][\w'’]*(?:[- ][A-Z][\w'’]+)?,\s+[A-Z]\.")
SPACED_DOI = re.compile(r"(?:https?:\s*//\s*(?:dx\.\s*)?doi\.\s*org/|\bdoi:)\s*(10\.\s*\d{4,9}\s*/.*)$", re.I | re.S)


def parse_reference(raw, idx):
    m = NUM_PREFIX.match(raw)
    no = int(m.group(1) or m.group(2)) if m else idx
    text = raw[m.end():] if m else raw
    year, suffix = find_year(text)
    doi = find_doi(text)
    sd = SPACED_DOI.search(text)
    if sd:   # PDF layout splits DOIs with spaces: 'https:// doi. org/ 10. 1016/ 0749- 5978(91) 90020 - T.'
        joined = re.sub(r"\s+", "", sd.group(1)).rstrip(".,;")
        if len(joined) > len(doi or ""):
            doi = joined.lower()
    return {"no": no, "raw": text.strip(), "doi_in_text": doi, "surname": surname_key(text),
            "year": year, "year_suffix": suffix}


def parse_bibtex(text):
    refs = []
    for i, m in enumerate(re.finditer(r"@\w+\s*\{\s*([^,]+),(.*?)\n\}", text, re.S), 1):
        fields = dict((k.lower(), squash(v.strip("{}\" "))) for k, v in
                      re.findall(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|\d+)", m.group(2)))
        author = fields.get("author", "").split(" and ")[0]
        raw = f'{fields.get("author", "")} ({fields.get("year", "")}). {fields.get("title", "")}. {fields.get("journal", fields.get("booktitle", ""))}'
        if fields.get("doi"):
            raw += f" https://doi.org/{fields['doi']}"
        ref = parse_reference(raw, i)
        ref["surname"] = surname_key(author) if "," in author else surname_key(author.split(" ")[-1])
        ref["bibkey"] = m.group(1).strip()
        refs.append(ref)
    return refs


# --- in-text citations ----------------------------------------------------------

PAREN = re.compile(r"\(([^()]{2,400}?\b(?:1[89]\d{2}|20\d{2})[a-z]?[^()]{0,200})\)")
NARRATIVE = re.compile(r"((?:[A-Z][\w'’\-]+\s+){0,2}[A-Z][\w'’\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][\w'’\-]+)?)\s*"
                       r"\(\s*((?:1[89]\d{2}|20\d{2})[a-z]?(?:\s*,\s*(?:1[89]\d{2}|20\d{2})[a-z]?)*)\s*\)")
NUMERIC = re.compile(r"\[(\d{1,4}(?:\s*[-–,]\s*\d{1,4})*)\]")
YEAR_TOKEN = re.compile(r"(?<![\d.,])\b(1[89]\d{2}|20\d{2})([a-z]?)\b(?![.,]\d)")
PREFIXES = re.compile(r"^\s*(?:e\.g\.,?|i\.e\.,?|see(?: also)?|cf\.|for (?:a )?review(?:s)?,? see|but see|as in|reviewed in)\s*", re.I)


def _expand_numeric(s):
    out = []
    for part in re.split(r"\s*,\s*", s):
        if re.search(r"[-–]", part):
            a, b = [int(x) for x in re.split(r"\s*[-–]\s*", part)[:2]]
            if 0 < b - a < 50:
                out.extend(range(a, b + 1))
        elif part.strip().isdigit():
            out.append(int(part))
    return out


def _section_map(body):
    """(offset, heading, paragraph) marks so a citation can be located as '§ heading ¶n'."""
    heads, para, pos, in_para = [], 0, 0, False
    marked = bool(re.search(r"^\s*#", body, re.M))   # explicit headings present: trust only those
    for line in body.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("#") or (not marked and 0 < len(s) < 70 and not s.endswith((".", ":", ";", ",")) and s[:1].isupper()
                                 and len(s.split()) <= 8 and not re.search(r"\(\d{4}\)|\[\d", s) and not in_para):
            heads.append((pos, s.lstrip("# ").strip(), None))
            para, in_para = 0, False
        elif s:
            if not in_para:
                para += 1
                heads.append((pos, None, para))
            in_para = True
        else:
            in_para = False
        pos += len(line)
    return heads


def _locate(heads, offset):
    heading, para = "Body", 0
    for pos, h, p in heads:
        if pos > offset:
            break
        if h:
            heading, para = h, 0
        elif p:
            para = p
    return f"§ {heading} ¶{para}"


def find_citations(body, refs):
    """Return citation records: marker, sentence, location, ref_nos (possibly empty = dangling)."""
    spans = sentence_spans(body)
    heads = _section_map(body)
    by_author = {}
    for r in refs:
        by_author.setdefault((r["surname"], r["year"]), []).append(r)
    numbers = {r["no"] for r in refs}
    cites = []

    def add(start, marker, ref_nos, key=None, conflict=None):
        sent = next((body[a:b].strip() for a, b in spans if a <= start < b), "")
        cites.append({"marker": marker, "sentence": squash(sent), "offset": start,
                      "location": _locate(heads, start), "ref_nos": sorted(set(ref_nos)), "key": key,
                      "conflict": conflict})

    numeric_hits = list(NUMERIC.finditer(body))
    if numeric_hits and len(numbers) and sum(1 for m in numeric_hits if set(_expand_numeric(m.group(1))) <= numbers) >= len(numeric_hits) / 2:
        style = "numeric"
        for m in numeric_hits:
            nos = _expand_numeric(m.group(1))
            add(m.start(), m.group(0), [n for n in nos if n in numbers],
                key=None if all(n in numbers for n in nos) else f"[{','.join(str(n) for n in nos if n not in numbers)}]")
    else:
        style = "author-year"

        def resolve(chunk, year, suffix):
            sk = surname_key(PREFIXES.sub("", chunk))
            hits = by_author.get((sk, year), [])
            if len(hits) > 1 and suffix:
                hits = [h for h in hits if h["year_suffix"] == suffix] or hits
            if not hits:   # same author, list year one off: one citation with a year conflict, not two problems
                near = by_author.get((sk, year - 1), []) + by_author.get((sk, year + 1), [])
                if len(near) == 1:
                    return sk, [near[0]["no"]], {"text": f"{sk} {year}{suffix}", "ref_no": near[0]["no"],
                                                  "text_year": year, "list_year": near[0]["year"]}
            return sk, [h["no"] for h in hits], None

        for m in PAREN.finditer(body):
            for part in m.group(1).split(";"):
                years = list(YEAR_TOKEN.finditer(part))
                if not years:
                    continue
                chunk = part[:years[0].start()]
                if not re.search(r"[A-Z]", chunk) or re.search(r"\d", chunk):
                    continue   # authors carry no digits: '(β = 0.09, CI [0.2050])', '(No. 004-01/24, October 30, 2024)'

                for y in years:
                    sk, nos, conflict = resolve(chunk, int(y.group(1)), y.group(2))
                    add(m.start(), f"({squash(part)})", nos, key=f"{sk} {y.group(1)}{y.group(2)}", conflict=conflict)
        for m in NARRATIVE.finditer(body):
            for y in YEAR_TOKEN.finditer(m.group(2)):
                sk, nos, conflict = resolve(m.group(1), int(y.group(1)), y.group(2))
                if nos or sk:
                    add(m.start(), squash(m.group(0)), nos, key=f"{sk} {y.group(1)}{y.group(2)}", conflict=conflict)
    # merge markers that sit in the same sentence
    merged = {}
    for c in cites:
        k = (c["sentence"], c["location"])
        if k in merged:
            merged[k]["ref_nos"] = sorted(set(merged[k]["ref_nos"]) | set(c["ref_nos"]))
            merged[k]["markers"].append(c["marker"])
            if not c["ref_nos"] and c["key"]:
                merged[k]["dangling"].append(c["key"])
            if c["conflict"]:
                merged[k]["year_conflicts"].append(c["conflict"])
        else:
            merged[k] = {"sentence": c["sentence"], "location": c["location"], "offset": c["offset"],
                         "ref_nos": c["ref_nos"], "markers": [c["marker"]],
                         "dangling": [c["key"]] if (not c["ref_nos"] and c["key"]) else [],
                         "year_conflicts": [c["conflict"]] if c["conflict"] else []}
    out = sorted(merged.values(), key=lambda c: c["offset"])
    for i, c in enumerate(out, 1):
        c["cid"] = f"C{i:02d}"
    return out, style


def normalize_spaces(text):
    """Non-breaking and thin spaces (common in PDFs and Word: 'et\u00a0al.') become plain spaces."""
    return re.sub(r"[\u00a0\u2000-\u200a\u202f\u205f]", " ", text or "")


def parse_manuscript(text, bib_text=None):
    text = normalize_spaces(text)
    body, raw_refs = split_references(text)
    refs = parse_bibtex(bib_text) if bib_text else [parse_reference(r, i) for i, r in enumerate(raw_refs, 1)]
    citations, style = find_citations(body, refs)
    return {"body": body, "references": refs, "citations": citations, "style": style}
