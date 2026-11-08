"""OpenAlex: retraction flag, abstracts, author IDs, reference lists, batch lookups.

OpenAlex requires a free API key since February 2026 (sent as `api_key`). Without one this
module is skipped and the run's coverage block says so.
"""
import urllib.parse

from ..textutil import clean_doi

BASE = "https://api.openalex.org"
SELECT = ("id,doi,display_name,publication_year,type,is_retracted,authorships,primary_location,"
          "abstract_inverted_index,open_access,cited_by_count,referenced_works")


def enabled(ctx):
    return bool(ctx.cfg.openalex_key)


def _p(ctx, extra=None):
    p = {"api_key": ctx.cfg.openalex_key}
    p.update(extra or {})
    return p


def abstract_from_index(inv):
    if not inv:
        return None
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def normalize(w):
    src = ((w.get("primary_location") or {}).get("source") or {})
    return {
        "doi": clean_doi(w.get("doi")),
        "openalex_id": (w.get("id") or "").rsplit("/", 1)[-1] or None,
        "title": w.get("display_name"),
        "year": w.get("publication_year"),
        "venue": src.get("display_name"),
        "oa_type": w.get("type"),
        "is_retracted": bool(w.get("is_retracted")),
        "authors": [{"family": (a.get("author") or {}).get("display_name", "").split(" ")[-1],
                     "given": " ".join((a.get("author") or {}).get("display_name", "").split(" ")[:-1]),
                     "orcid": ((a.get("author") or {}).get("orcid") or "").rsplit("/", 1)[-1] or None,
                     "openalex_author": ((a.get("author") or {}).get("id") or "").rsplit("/", 1)[-1] or None}
                    for a in w.get("authorships") or []],
        "abstract": abstract_from_index(w.get("abstract_inverted_index")),
        "oa_url": (w.get("open_access") or {}).get("oa_url"),
        "cited_by_count": w.get("cited_by_count"),
        "referenced_works": [r.rsplit("/", 1)[-1] for r in w.get("referenced_works") or []],
    }


def work_by_doi(ctx, doi):
    if not enabled(ctx):
        return None
    data = ctx.http.get_json(f"{BASE}/works/doi:{urllib.parse.quote(doi, safe='/')}", _p(ctx, {"select": SELECT}))
    return normalize(data) if data else None


def retracted_among(ctx, openalex_ids):
    """Return the subset of OpenAlex work IDs flagged as retracted (batched, 50 per call)."""
    if not enabled(ctx) or not openalex_ids:
        return []
    out = []
    ids = list(dict.fromkeys(openalex_ids))
    for i in range(0, len(ids), 50):
        chunk = "|".join(ids[i:i + 50])
        data = ctx.http.get_json(f"{BASE}/works", _p(ctx, {
            "filter": f"openalex:{chunk},is_retracted:true", "select": "id,doi,display_name", "per-page": 50}))
        for w in (data or {}).get("results", []):
            out.append({"openalex_id": w["id"].rsplit("/", 1)[-1], "doi": clean_doi(w.get("doi")),
                        "title": w.get("display_name")})
    return out


def search(ctx, query, n=20):
    if not enabled(ctx):
        return []
    data = ctx.http.get_json(f"{BASE}/works", _p(ctx, {"search": query, "per-page": n, "select": SELECT}))
    return [normalize(w) for w in (data or {}).get("results", [])]
