"""Crossref REST API: DOI metadata, bibliographic search, editorial notices, preprint relations.

Since 2025 Crossref carries Retraction Watch data: a retracted work lists the notice in
`updated-by` with `source` = 'publisher' or 'retraction-watch'.
"""
import urllib.parse

from ..textutil import clean_doi, strip_tags

BASE = "https://api.crossref.org"
RETRACTING = {"retraction", "withdrawal", "removal"}
NOTICE_TYPES = RETRACTING | {"partial_retraction", "expression_of_concern", "correction", "corrigendum",
                             "erratum", "addendum", "clarification"}


def _params(ctx, extra=None):
    p = {"mailto": ctx.cfg.mailto} if ctx.cfg.mailto else {}
    p.update(extra or {})
    return p


def work(ctx, doi, max_age=None):
    data = ctx.http.get_json(f"{BASE}/works/{urllib.parse.quote(doi, safe='/')}", _params(ctx), max_age=max_age)
    return normalize(data["message"]) if data else None


def search(ctx, text, rows=3):
    data = ctx.http.get_json(f"{BASE}/works", _params(ctx, {"query.bibliographic": text[:600], "rows": rows}))
    return [normalize(it) for it in (data or {}).get("message", {}).get("items", [])]


def _date(d):
    parts = (d or {}).get("date-parts") or [[None]]
    return "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(parts[0]) if x) or None


def normalize(m):
    year = None
    for k in ("issued", "published-print", "published-online", "published", "created"):
        dp = (m.get(k) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            year = int(dp[0][0])
            break
    notices = []
    for u in m.get("updated-by") or []:
        t = (u.get("type") or "").lower().replace("-", "_")
        if t in NOTICE_TYPES:
            notices.append({"type": t, "doi": clean_doi(u.get("DOI")), "date": _date(u.get("updated")),
                            "source": u.get("source") or "publisher", "label": u.get("label") or t})
    rel = m.get("relation") or {}
    title = strip_tags(" ".join(m.get("title") or []))
    refs = [clean_doi(r.get("DOI")) for r in m.get("reference") or [] if r.get("DOI")]
    return {
        "doi": clean_doi(m.get("DOI")),
        "title": title,
        "authors": [{"family": a.get("family") or a.get("name") or "", "given": a.get("given") or "",
                     "orcid": (a.get("ORCID") or "").rsplit("/", 1)[-1] or None}
                    for a in m.get("author") or []],
        "year": year,
        # online-first, print and issue years differ; a citation may legitimately use any of them
        "years": sorted({int(d[0][0]) for d in ((m.get(k) or {}).get("date-parts") for k in
                         ("issued", "published-print", "published-online", "published")) if d and d[0] and d[0][0]}
                        | ({int(((m.get("journal-issue") or {}).get("published-print") or {}).get("date-parts", [[0]])[0][0])}
                           if ((m.get("journal-issue") or {}).get("published-print") or {}).get("date-parts") else set()) - {0}),
        "venue": strip_tags(" ".join(m.get("container-title") or [])) or None,
        "type": m.get("type"),
        "subtype": m.get("subtype"),
        "abstract": strip_tags(m.get("abstract")) or None,
        "notices": notices,
        "is_retracted": any(n["type"] in RETRACTING for n in notices) or title.upper().startswith("RETRACTED"),
        "preprint_of": [clean_doi(r.get("id")) for r in rel.get("is-preprint-of", []) if r.get("id-type") == "doi"],
        "has_preprint": [clean_doi(r.get("id")) for r in rel.get("has-preprint", []) if r.get("id-type") == "doi"],
        "references": refs,
        "reference_count": m.get("reference-count") or m.get("references-count"),
        "cited_by_count": m.get("is-referenced-by-count"),
        "score": m.get("score"),
        "url": m.get("URL"),
        "sources": ["crossref"],
    }


def doi_registered(ctx, doi):
    """Ask the DOI handle system directly; catches DataCite/mEDRA DOIs that Crossref does not hold."""
    data = ctx.http.get_json(f"https://doi.org/api/handles/{urllib.parse.quote(doi, safe='/')}")
    return bool(data and data.get("responseCode") == 1)
