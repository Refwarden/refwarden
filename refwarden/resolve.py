"""Reference string -> verified work record, with an explicit match verdict.

Statuses
  verified        DOI resolves (or search match) and title, year and first author agree
  probable        closest search match is only a partial title match; needs a human look
  mismatch        the work exists but the reference misstates year / first author / title
  doi_mismatch    the DOI given resolves to a different work
  doi_unknown     the DOI given is not registered anywhere
  doi_elsewhere   the DOI is registered (e.g. DataCite) but Crossref has no metadata to compare
  not_found       no candidate close enough: possibly fabricated, or not indexed (books, grey literature)
"""
import re

from .sources import crossref, europepmc, openalex, s2
from .textutil import containment, fold, surname_key

GREY_HINTS = ("report", "working paper", "press", "retrieved", "available at", "http", "www.", "thesis",
              "dissertation", "white paper", "blog", "policy brief", "act of", "regulation", "directive")


def compare(ref, work):
    """Field-by-field comparison of what the manuscript says with the registry record."""
    diffs = []
    title_fit = containment(work.get("title") or "", ref["raw"])
    stated = stated_title(ref["raw"])
    if stated and work.get("title"):
        # both directions: a short record title ('Anonymity and Confidentiality') fits inside a longer cited
        # title, so the cited title must also be covered by the record's title (subtitles aside)
        main = re.split(r"[:?]", stated)[0]
        title_fit = min(title_fit, max(containment(stated, work["title"]), containment(main, work["title"])))
    if work.get("year") and ref.get("year") and ref["year"] not in (work.get("years") or [work["year"]]):
        # one year off is often online-first versus print; still reported, at lower severity
        diffs.append({"field": "year", "manuscript": ref["year"], "record": work["year"],
                      "off_by_one": abs(work["year"] - ref["year"]) == 1})
    first = (work.get("authors") or [{}])[0]
    rec_surname = fold(first.get("family") or "").split(" ")[-1].strip(".") if first else ""
    if rec_surname and ref.get("surname") and ref["surname"] != rec_surname and ref["surname"] not in fold(first.get("family", "")):
        # tolerate author-order variants: the stated first author must at least be an author
        all_names = " ".join(fold(a.get("family") or "") for a in work.get("authors") or [])
        diffs.append({"field": "first_author", "manuscript": ref["surname"], "record": first.get("family"),
                      "is_coauthor": ref["surname"] in all_names})
    return title_fit, diffs


def stated_title(raw):
    """The title as the reference states it: in quotes (Chicago, Harvard) or the sentence after the year (APA)."""
    m = re.search(r"[“\"]([^”\"]{10,300})[”\"]", raw)
    if m:
        return m.group(1)
    m = re.search(r"(?:\(\d{4}[a-z]?\)|\b\d{4}[a-z]?)\.\s+([^.?]{10,300}[.?])", raw)
    return m.group(1) if m and len(m.group(1).split()) >= 3 else None


def _raw_has_title(ref):
    return len([t for t in ref["raw"].split() if len(t) > 3]) >= 6


def resolve(ctx, ref):
    out = {"status": "not_found", "match": {}, "work": None}
    doi = ref.get("doi_in_text")
    if doi:
        w = crossref.work(ctx, doi)
        if w:
            fit, diffs = compare(ref, w)
            out["work"] = w
            if _raw_has_title(ref) and fit < 0.3:
                out["status"] = "doi_mismatch"
            else:
                out["status"] = "mismatch" if diffs else "verified"
            out["match"] = {"method": "doi", "title_fit": round(fit, 2), "diffs": diffs}
            return out
        registered = crossref.doi_registered(ctx, doi)
        out["status"] = "doi_elsewhere" if registered else "doi_unknown"
        out["match"] = {"method": "doi", "registered": registered}
        if registered:
            s = s2.paper(ctx, doi)
            if s:
                out["work"] = dict(s, doi=doi, sources=["semanticscholar"], notices=[], is_retracted=False)
            return out
    # bibliographic search
    candidates = crossref.search(ctx, ref["raw"], rows=5)
    best, best_score = None, 0.0
    for c in candidates:
        fit, diffs = compare(ref, c)
        score = fit - 0.25 * len(diffs)
        if score > best_score:
            best, best_score = (c, fit, diffs), score
    if best and any(d["field"] == "first_author" and not d.get("is_coauthor") for d in best[2]):
        # a title match with a different author team is a different work, not a near miss: fabricated
        # references reuse plausible title words, and book reviews carry the reviewed book's full title
        w, fit, _ = best
        out["match"] = {"method": "search", "best_title_fit": round(fit, 2),
                        "closest": {"title": w.get("title"), "doi": w.get("doi"), "first_author": (w.get("authors") or [{}])[0].get("family")},
                        "grey_hint": any(h in ref["raw"].lower() for h in GREY_HINTS)}
        return out
    if best and best[1] >= 0.6:
        w, fit, diffs = best
        out["work"] = w
        out["status"] = "verified" if (fit >= 0.85 and not diffs) else "mismatch" if fit >= 0.85 else "probable"
        out["match"] = {"method": "search", "title_fit": round(fit, 2), "diffs": diffs, "crossref_score": w.get("score")}
        if doi and w.get("doi") != doi:
            out["status"] = "mismatch"
            out["match"]["diffs"].append({"field": "doi", "manuscript": doi, "record": w["doi"]})
    else:
        out["match"] = {"method": "search", "best_title_fit": round(best[1], 2) if best else 0.0,
                        "grey_hint": any(h in ref["raw"].lower() for h in GREY_HINTS)}
    return out


def enrich(ctx, work, coverage):
    """Add abstract, author IDs, reference IDs and retraction flags from secondary sources."""
    doi = work.get("doi")
    if not doi:
        return work
    extra = []
    for name, fn in (("openalex", openalex.work_by_doi), ("semanticscholar", s2.paper), ("europepmc", europepmc.find)):
        if name == "openalex" and not openalex.enabled(ctx):
            coverage["skipped"]["openalex"] = "no OPENALEX_API_KEY (free key required since Feb 2026)"
            continue
        try:
            rec = fn(ctx, doi)
        except Exception as e:
            coverage["errors"].append(f"{name}: {e}"[:200])
            rec = None
        if rec:
            extra.append((name, rec))
    abstracts = [(work.get("abstract") or "", "crossref")]
    for name, rec in extra:
        if rec.get("abstract"):
            abstracts.append((rec["abstract"], name))
    for name, rec in extra:
        work.setdefault("sources", []).append(name)
        for k in ("abstract", "oa_url", "openalex_id", "s2_id", "pmid", "pmcid", "epmc_id", "epmc_source",
                  "arxiv", "referenced_works", "publication_types"):
            if rec.get(k) and not work.get(k):
                work[k] = rec[k]
        if name == "openalex":
            if rec.get("is_retracted"):
                work["is_retracted"] = True
                work.setdefault("notices", []).append({"type": "retraction", "doi": None, "date": None,
                                                       "source": "openalex", "label": "OpenAlex is_retracted"})
            ids = {a.get("orcid") for a in rec.get("authors") or []}
            if any(ids):
                work["author_ids"] = sorted(i for i in ids if i)
        if name == "semanticscholar" and rec.get("doi") and rec["doi"] != doi and not work.get("published_doi"):
            work["published_doi"] = rec["doi"]   # arXiv DOI cited; S2 knows a journal DOI
    # Registries disagree on what an abstract is (Science's Crossref record carries the editor's summary;
    # Europe PMC carries the author abstract), so every distinct version is kept and searched.
    kept, seen = [], []
    for text, name in sorted(abstracts, key=lambda x: -len(x[0])):
        key = fold(text)[:120]
        if text.strip() and not any(key in s or s[:120] in fold(text) for s in seen):
            kept.append(text.strip())
            seen.append(fold(text))
    work["abstract"] = "\n\n".join(kept) or None
    work["access"] = "abstract" if work.get("abstract") else "metadata-only"
    return work


def work_label(w):
    if not w:
        return "?"
    first = (w.get("authors") or [{}])[0].get("family") or "Anon."
    etal = " et al." if len(w.get("authors") or []) > 2 else ""
    return f"{first}{etal} ({w.get('year') or 'n.d.'})"


def ref_label(ref):
    return (surname_key(ref["raw"]) or "ref").title() + f" ({ref.get('year') or 'n.d.'})"
