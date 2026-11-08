"""Internal cross-reference check: figures, tables, sections, appendices, equations, boxes.

Rule-based, offline. Finds what the text points to and what the document defines, then reports:
  missing      the text mentions Figure 4 but no Figure 4 caption exists
  unmentioned  a captioned figure or table is never mentioned in the text
  order        Figure 3 is first mentioned before Figure 2
  numbering    captions skip a number (1, 2, 4) or repeat one (two Table 2 captions)
  field-error  Word's 'Error! Reference source not found.' or LaTeX's '??' left in the text
Works on .docx / .md / .txt / PDF text. Captions are paragraphs that start 'Figure 3.' / 'Table 2:' etc.
"""
import re

from .parse import END_OF_REFS, REF_HEADING, _locate, _section_map
from .textutil import sentence_spans, squash

KINDS = {
    "figure": r"Figures?|Figs?\.?",
    "table": r"Tables?|Tabs?\.",
    "section": r"Sections?|Sects?\.|§§?",
    "appendix": r"Appendix|Appendices|App\.",
    "equation": r"Equations?|Eqs?\.|Eqns?\.",
    "box": r"Box(?:es)?",
}
LABEL = r"\(?(?:[A-Z]?\d+(?:\.\d+)*[a-z]?|[A-Z](?![a-z]))\)?"
LIST = rf"{LABEL}(?:\s*(?:,|and|&|or|–|—|-|to)\s*{LABEL})*"
MENTION = re.compile(rf"(?<!\w)((?i:{'|'.join(f'(?P<{k}>{v})' for k, v in KINDS.items())}))\s*({LIST})")
CAPTION = re.compile(r"^\s*(?:[#>*_]+\s*)*(?:\*\*|__)?\s*(Figure|Fig\.?|Table|Box|Exhibit)\s+([A-Z]?S?\d+(?:\.\d+)*[a-z]?)\s*(?:\*\*|__)?\s*[.:|—–-]\s*(?=\S.{2,})", re.I)
MD_IMAGE_CAPTION = re.compile(r"!\[\s*(Figure|Fig\.?)\s+([A-Z]?S?\d+)", re.I)
SECTION_HEAD = re.compile(r"^\s*(?:#+\s*)?(\d+(?:\.\d+)*)\.?\s+[A-Z][^\n]{0,90}$")
APPENDIX_HEAD = re.compile(r"^\s*(?:#+\s*)?Appendix(?:\s+([A-Z]\d*|\d+))?\b[^\n]{0,90}$", re.I)
EQUATION_LABEL = re.compile(r"[=≤≥<>∑∫^_\\].{0,200}?\((\d+[a-z]?)\)\s*$")
WORD_FIELD_ERROR = re.compile(r"Error!\s+(?:Reference source not found|Bookmark not defined|No text of specified style in document|"
                              r"Not a valid bookmark self-reference)\.?")
LATEX_UNRESOLVED = re.compile(r"\b(?:Figure|Fig\.|Table|Section|Sec\.|Eq\.|Equation|Appendix|Chapter)\s*~?\s*\?\?|\[\?\]")
KIND_OF_CAPTION = {"figure": "figure", "fig": "figure", "fig.": "figure", "table": "table", "box": "box", "exhibit": "figure"}


def _labels(raw):
    """'2–4' -> ['2','3','4'];  '2a and 3' -> ['2a','3'];  '(5)' -> ['5'];  'A' -> ['A']."""
    raw = raw.replace("(", "").replace(")", "")
    out = []
    parts = re.split(r"\s*(,|and|&|or|–|—|-|to)\s*", raw)
    i = 0
    while i < len(parts):
        tok = parts[i].strip()
        if tok and tok not in (",", "and", "&", "or", "–", "—", "-", "to"):
            sep = parts[i + 1] if i + 1 < len(parts) else ""
            nxt = parts[i + 2].strip() if i + 2 < len(parts) else ""
            if sep in ("–", "—", "-", "to") and tok.isdigit() and nxt.isdigit() and 0 < int(nxt) - int(tok) < 30:
                out.extend(str(n) for n in range(int(tok), int(nxt) + 1))
                i += 3
                continue
            out.append(tok)
        i += 1
    return out


def _base(label, kind):
    """Figure 3a -> 3 (panels belong to their figure)."""
    return re.sub(r"(?<=\d)[a-z]$", "", label) if kind in ("figure", "table", "box") else label


def _mask_references(text):
    """Blank out the reference list (same length, so offsets stay valid): titles there are not cross-references."""
    m = list(REF_HEADING.finditer(text))
    if not m:
        return text
    start = m[-1].start()
    end = len(text)
    pos = m[-1].end()
    for line in text[pos:].splitlines(keepends=True):
        if END_OF_REFS.match(line) and len(line.strip()) < 60:
            end = pos
            break
        pos += len(line)
    return text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]


def analyze(text):
    from .parse import normalize_spaces
    scan = _mask_references(normalize_spaces(text))
    heads = _section_map(scan)
    defined = {k: {} for k in KINDS}       # kind -> label -> [offsets]
    mentions = {k: {} for k in KINDS}      # kind -> label -> [offsets]
    caption_lines, pos = set(), 0
    for line in scan.splitlines(keepends=True):
        s = line.strip()
        m = CAPTION.match(line) or MD_IMAGE_CAPTION.search(line)
        if m:
            kind = KIND_OF_CAPTION[m.group(1).lower().rstrip(".")]
            defined[kind].setdefault(m.group(2), []).append(pos)
            caption_lines.add(pos)
        elif s and len(s) < 100 and not s.endswith(".") and len(s.split()) <= 12:
            sm, am = SECTION_HEAD.match(line), APPENDIX_HEAD.match(line)
            if am:
                defined["appendix"].setdefault((am.group(1) or "").upper(), []).append(pos)
            elif sm:
                defined["section"].setdefault(sm.group(1), []).append(pos)
        em = EQUATION_LABEL.search(s)
        if em:
            defined["equation"].setdefault(em.group(1), []).append(pos)
        pos += len(line)

    line_starts = sorted(caption_lines)
    caption_spans = []
    for st in line_starts:
        nl = scan.find("\n", st)
        caption_spans.append((st, len(scan) if nl < 0 else nl))
    for m in MENTION.finditer(scan):
        if any(a <= m.start() < b for a, b in caption_spans):
            continue
        kind = next(k for k in KINDS if m.group(k))
        for lab in _labels(m.group(len(KINDS) + 2)):
            if kind == "appendix" and not re.fullmatch(r"[A-Z]\d*|\d+", lab):
                continue
            if kind in ("figure", "table", "box", "equation") and not re.search(r"\d", lab):
                continue
            mentions[kind].setdefault(_base(lab, kind), []).append(m.start())

    issues, notes = [], []

    def where(off):
        return _locate(heads, off)

    spans = sentence_spans(scan)

    def snippet(off):
        sent = next((scan[x:y] for x, y in spans if x <= off < y), scan[off:off + 120])
        return squash(sent)[:240]

    for kind in ("figure", "table", "box"):
        d, mt = defined[kind], mentions[kind]
        label = kind.title()
        if mt and not d:
            nums = sorted(mt, key=lambda x: (len(x), x))
            notes.append(f"{label}s {', '.join(nums)} are mentioned but no {kind} captions were found; "
                         f"{kind} references were not checked (captions may sit in separate files)")
            issues.append({"kind": kind, "type": "no-captions", "labels": nums, "offset": mt[nums[0]][0]})
            continue
        for lab, offs in sorted(mt.items()):
            if lab not in d:
                issues.append({"kind": kind, "type": "missing", "labels": [lab], "offset": offs[0]})
        for lab, offs in sorted(d.items()):
            if _base(lab, kind) not in mt and lab not in mt:
                issues.append({"kind": kind, "type": "unmentioned", "labels": [lab], "offset": offs[0]})
            if len(offs) > 1:
                issues.append({"kind": kind, "type": "duplicate", "labels": [lab], "offset": offs[1]})
        main = sorted(int(l) for l in d if l.isdigit())
        if main:
            gaps = [n for n in range(1, main[-1] + 1) if n not in main]
            if gaps:
                issues.append({"kind": kind, "type": "gap", "labels": [str(g) for g in gaps], "offset": d[str(main[0])][0]})
        firsts = sorted((offs[0], int(l)) for l, offs in mt.items() if l.isdigit())
        seen_max = 0
        for off, n in firsts:
            if n < seen_max:
                issues.append({"kind": kind, "type": "order", "labels": [str(n)], "offset": off, "after": str(seen_max)})
            seen_max = max(seen_max, n)
    if len(defined["section"]) >= 2:
        for lab, offs in sorted(mentions["section"].items()):
            if lab not in defined["section"]:
                issues.append({"kind": "section", "type": "missing", "labels": [lab], "offset": offs[0]})
    elif mentions["section"]:
        notes.append("Section numbers are mentioned but headings are not numbered; section references were not checked")
    for lab, offs in sorted(mentions["appendix"].items()):
        if lab not in defined["appendix"]:
            typ = "missing" if defined["appendix"] else "absent-appendix"
            issues.append({"kind": "appendix", "type": typ, "labels": [lab], "offset": offs[0]})
    if defined["equation"]:
        for lab, offs in sorted(mentions["equation"].items()):
            if lab not in defined["equation"]:
                issues.append({"kind": "equation", "type": "missing", "labels": [lab], "offset": offs[0]})
    elif mentions["equation"]:
        notes.append("Equation numbers are mentioned but no numbered display equations were recognised; not checked")
    for rx, typ in ((WORD_FIELD_ERROR, "word-field-error"), (LATEX_UNRESOLVED, "latex-unresolved")):
        for m in rx.finditer(scan):
            issues.append({"kind": "field", "type": typ, "labels": [m.group(0)], "offset": m.start()})
    def line_at(off):
        start = scan.rfind("\n", 0, off) + 1
        end = scan.find("\n", off)
        return squash(scan[start:len(scan) if end < 0 else end])[:240]

    for it in issues:
        it["location"] = where(it["offset"])
        on_line = it["type"] in ("duplicate", "gap", "unmentioned", "word-field-error", "latex-unresolved")
        it["context"] = line_at(it["offset"]) if on_line else snippet(it["offset"])
    inventory = {k: {"defined": sorted(defined[k], key=lambda x: (len(x), x)),
                     "mentioned": sorted(mentions[k], key=lambda x: (len(x), x))}
                 for k in KINDS if defined[k] or mentions[k]}
    return {"inventory": inventory, "issues": issues, "notes": notes}


TEMPLATES = {
    # type: (defect, consequence, severity, confidence, repair)
    "missing": ("The text refers to {K} {L}, which does not exist in the document",
                "Readers and reviewers cannot find what the sentence points to",
                "Moderate", "Confirmed", "Correct the number, or add the missing {k}"),
    "unmentioned": ("{K} {L} is never mentioned in the text",
                    "Journals require every {k} to be cited in the text; an unmentioned {k} reads as left over from an earlier draft",
                    "Minor", "Confirmed", "Mention {K} {L} where the text relies on it, or remove it"),
    "duplicate": ("Two captions carry the same number, {K} {L}",
                  "References to {K} {L} are ambiguous and later numbering is off by one",
                  "Moderate", "Confirmed", "Renumber the {k}s and update every mention"),
    "gap": ("{K} numbering skips {L}",
            "A missing number signals a deleted or misplaced {k}; mentions may point to the wrong one",
            "Minor", "Confirmed", "Renumber the {k}s consecutively and update every mention"),
    "order": ("{K} {L} is first mentioned after {K} {A}",
              "Most journals require {k}s to be numbered in order of first mention",
              "Minor", "Confirmed", "Renumber so the first mention of each {k} follows numeric order"),
    "no-captions": ("{K}s {L} are mentioned but no {k} captions were found in the document",
                    "If the {k}s are submitted as separate files this is expected; otherwise the {k}s are missing",
                    "Minor", "Question", "Confirm the {k}s and their captions are included in the submission"),
    "absent-appendix": ("The text refers to Appendix {L}, but the document contains no appendix",
                        "If the appendix is a separate supplementary file this is expected; otherwise readers cannot find it",
                        "Minor", "Question", "Include Appendix {L}, or name the supplementary file it is in"),
    "word-field-error": ("A broken Word cross-reference field is printed in the text",
                         "The reader sees 'Error! …' instead of a figure, table or section number",
                         "Moderate", "Confirmed", "Re-insert the cross-reference in Word (References → Cross-reference) and update fields"),
    "latex-unresolved": ("An unresolved LaTeX reference ('??' or '[?]') is printed in the text",
                         "The reader sees '??' instead of a number or citation",
                         "Moderate", "Confirmed", "Fix the label or citation key and recompile until no undefined references remain"),
}


def to_findings(result, make):
    """Turn analyze() output into contract rows via the engine's finding constructor `make`."""
    out = []
    for it in result["issues"]:
        defect, cons, sev, conf, repair = TEMPLATES[it["type"]]
        k = it["kind"]
        fmt = {"K": k.title(), "k": k, "L": ", ".join(it["labels"]), "A": it.get("after", "")}
        out.append(make("cross-reference", it["location"], defect.format(**fmt),
                        f"\"{it['context']}\"", cons.format(**fmt), sev, conf, repair.format(**fmt),
                        method="manuscript", pass_="XREF"))
    return out
