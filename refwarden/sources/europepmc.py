"""Europe PMC: abstracts, citing articles, and citation statements read from open-access full text.

For open-access citing papers the JATS full text is parsed directly: the reference entry that
points at the cited work is located, and every sentence carrying an <xref> to it is returned
together with the section it sits in (introduction / methods / results / discussion).
"""
import re
import xml.etree.ElementTree as ET

from ..textutil import clean_doi, containment, sentence_spans, squash

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MARK = "⁣"   # invisible separator used to tag xref positions


def find(ctx, doi):
    data = ctx.http.get_json(f"{BASE}/search", {"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"})
    res = ((data or {}).get("resultList") or {}).get("result") or []
    if not res:
        return None
    r = res[0]
    return {"epmc_id": r.get("id"), "epmc_source": r.get("source"), "pmid": r.get("pmid"), "pmcid": r.get("pmcid"),
            "title": r.get("title"), "abstract": r.get("abstractText"), "year": int(r["pubYear"]) if r.get("pubYear") else None,
            "is_oa": r.get("isOpenAccess") == "Y", "doi": clean_doi(r.get("doi"))}


def citing_open_access(ctx, rec, n=10):
    """Open-access citing articles with full text in Europe PMC."""
    if not rec or not rec.get("epmc_id"):
        return []
    q = f'CITES:{rec["epmc_id"]}_{rec["epmc_source"]} AND OPEN_ACCESS:Y AND IN_EPMC:Y'
    data = ctx.http.get_json(f"{BASE}/search", {"query": q, "format": "json", "resultType": "lite", "pageSize": n})
    res = ((data or {}).get("resultList") or {}).get("result") or []
    return [{"pmcid": r.get("pmcid"), "title": r.get("title"), "year": int(r["pubYear"]) if r.get("pubYear") else None,
             "doi": clean_doi(r.get("doi"))} for r in res if r.get("pmcid")]


def search(ctx, query, n=20):
    data = ctx.http.get_json(f"{BASE}/search", {"query": query, "format": "json", "resultType": "core", "pageSize": n})
    res = ((data or {}).get("resultList") or {}).get("result") or []
    return [{"doi": clean_doi(r.get("doi")), "title": r.get("title"), "abstract": r.get("abstractText"),
             "year": int(r["pubYear"]) if r.get("pubYear") else None, "venue": (r.get("journalInfo") or {}).get("journal", {}).get("title"),
             "pmid": r.get("pmid")} for r in res]


# --- JATS parsing -----------------------------------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text_with_marks(el, rids):
    """Flatten an element to text, inserting MARK+rid+MARK after each bibliographic xref to one of `rids`."""
    out = [el.text or ""]
    for ch in el:
        tag = _local(ch.tag)
        if tag == "xref" and ch.get("ref-type") in ("bibr", None):
            out.append("".join(ch.itertext()))
            hit = [r for r in (ch.get("rid") or "").split() if r in rids]
            if hit:
                out.append(MARK + hit[0] + MARK)
        elif tag not in ("table-wrap", "fig", "disp-formula"):
            out.append(_text_with_marks(ch, rids))
        out.append(ch.tail or "")
    return "".join(out)


def section_class(title):
    t = (title or "").lower()
    for key, words in (("introduction", ("intro", "background")), ("methods", ("method", "material", "design", "procedure", "data")),
                       ("results", ("result", "finding")), ("discussion", ("discussion", "conclusion", "limitation", "implication"))):
        if any(w in t for w in words):
            return key
    return "other"


def target_rids(root, target):
    """IDs of <ref> entries that point at the target work (by DOI, PMID or title similarity)."""
    rids = set()
    for ref in root.iter():
        if _local(ref.tag) != "ref":
            continue
        text = " ".join(ref.itertext())
        ids = {(p.get("pub-id-type") or "").lower(): (p.text or "").strip() for p in ref.iter() if _local(p.tag) == "pub-id"}
        if target.get("doi") and (clean_doi(ids.get("doi")) == target["doi"] or target["doi"] in text.lower()):
            rids.add(ref.get("id"))
        elif target.get("pmid") and ids.get("pmid") == str(target["pmid"]):
            rids.add(ref.get("id"))
        elif target.get("title") and len(target["title"]) > 25:
            at = next((("".join(t.itertext())) for t in ref.iter() if _local(t.tag) == "article-title"), "")
            if at and containment(target["title"], at) >= 0.85:
                rids.add(ref.get("id"))
    rids.discard(None)
    return rids


def contexts_from_jats(xml_text, target):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    rids = target_rids(root, target)
    if not rids:
        return []
    body = next((e for e in root.iter() if _local(e.tag) == "body"), None)
    if body is None:
        return []
    out = []

    def walk(el, section):
        for ch in el:
            tag = _local(ch.tag)
            if tag == "sec":
                title_el = next((t for t in ch if _local(t.tag) == "title"), None)
                title = "".join(title_el.itertext()) if title_el is not None else ""
                walk(ch, section if section != "other" and section else section_class(title) if title else section)
            elif tag == "p":
                txt = squash(_text_with_marks(ch, rids))
                if MARK not in txt:
                    continue
                for a, b in sentence_spans(txt):
                    s = txt[a:b]
                    if MARK in s:
                        clean = squash(re.sub(MARK + r"[^" + MARK + "]*" + MARK, "", s))
                        out.append({"context": clean, "section": section or "other"})
    walk(body, None)
    return out


def fulltext_contexts(ctx, pmcid, target):
    xml_text = ctx.http.get_text(f"{BASE}/{pmcid}/fullTextXML")
    return contexts_from_jats(xml_text, target) if xml_text else []
