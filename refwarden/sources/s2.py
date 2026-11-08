"""Semantic Scholar Graph API: citation contexts (the citing sentences), intents, abstracts.

The `contexts` field on /paper/{id}/citations is the open equivalent of a citation statement.
Some publishers withhold contexts, so an empty list is common and is not an error.
"""
import urllib.parse

from ..textutil import clean_doi

BASE = "https://api.semanticscholar.org/graph/v1"
PAPER_FIELDS = "title,year,abstract,externalIds,authors,venue,citationCount,isOpenAccess,openAccessPdf,publicationTypes"


def _h(ctx):
    return {"x-api-key": ctx.cfg.s2_key} if ctx.cfg.s2_key else {}


def pid(doi):
    return "DOI:" + urllib.parse.quote(doi, safe="/")


def normalize(p):
    ext = p.get("externalIds") or {}
    return {
        "doi": clean_doi(ext.get("DOI")),
        "s2_id": p.get("paperId"),
        "arxiv": ext.get("ArXiv"),
        "pmid": ext.get("PubMed"),
        "title": p.get("title"),
        "year": p.get("year"),
        "venue": p.get("venue") or None,
        "abstract": p.get("abstract"),
        "authors": [{"family": (a.get("name") or "").split(" ")[-1],
                     "given": " ".join((a.get("name") or "").split(" ")[:-1]),
                     "s2_author": a.get("authorId")} for a in p.get("authors") or []],
        "cited_by_count": p.get("citationCount"),
        "oa_url": (p.get("openAccessPdf") or {}).get("url"),
        "publication_types": p.get("publicationTypes") or [],
    }


def paper(ctx, doi):
    data = ctx.http.get_json(f"{BASE}/paper/{pid(doi)}", {"fields": PAPER_FIELDS}, _h(ctx))
    return normalize(data) if data else None


def citations(ctx, doi, max_items=100):
    """Yield citing-paper records with contexts. Pages of 100, capped at max_items citing papers."""
    out, offset = [], 0
    fields = "contexts,intents,isInfluential,title,year,externalIds"
    while offset < max_items:
        limit = min(100, max_items - offset)
        data = ctx.http.get_json(f"{BASE}/paper/{pid(doi)}/citations",
                                 {"fields": fields, "offset": offset, "limit": limit}, _h(ctx))
        if not data:
            break
        for row in data.get("data") or []:
            cp = row.get("citingPaper") or {}
            out.append({
                "citing_doi": clean_doi((cp.get("externalIds") or {}).get("DOI")),
                "citing_title": cp.get("title"),
                "citing_year": cp.get("year"),
                "contexts": [c for c in row.get("contexts") or [] if c],
                "intents": row.get("intents") or [],
                "is_influential": bool(row.get("isInfluential")),
            })
        if data.get("next") is None:
            break
        offset = data["next"]
    return out


def search(ctx, query, n=20):
    data = ctx.http.get_json(f"{BASE}/paper/search", {"query": query[:300], "limit": n, "fields": PAPER_FIELDS}, _h(ctx))
    return [normalize(p) for p in (data or {}).get("data") or []]
