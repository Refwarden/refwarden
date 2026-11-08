"""Watchlist: alert when a paper you rely on gets a notice, a published version, or new contrasting citations."""
import json

from . import engine, stance
from .resolve import enrich, work_label
from .sources import crossref


def state_of(ctx, doi, contexts=100):
    w = crossref.work(ctx, doi, max_age=0)
    if not w:
        return None
    enrich(ctx, w, engine.coverage_block())
    sts = engine.gather_statements(ctx, w, contexts, False)
    stance.classify(sts, ctx.llm, w.get("title") or "")
    ctx.db.add_statements(sts)
    t = stance.tally(sts)
    return {"label": work_label(w), "notices": sorted(f"{n['type']}:{n.get('doi')}" for n in w.get("notices") or []),
            "retracted": bool(w.get("is_retracted")), "published_version": (w.get("preprint_of") or [None])[0],
            "cited_by": w.get("cited_by_count"), "contrasting": t["contrasting"], "supporting": t["supporting"]}


def add(ctx, doi, label=""):
    ctx.db.execute("INSERT OR IGNORE INTO watch(doi, label, added) VALUES (?,?,?)", (doi.lower(), label, engine.now()))


def run(ctx, contexts=100):
    alerts = []
    for row in ctx.db.query("SELECT * FROM watch"):
        new = state_of(ctx, row["doi"], contexts)
        if new is None:
            alerts.append({"doi": row["doi"], "change": "no longer resolves in Crossref"})
            continue
        old = json.loads(row["last_state"]) if row["last_state"] else None
        if old:
            for n in set(new["notices"]) - set(old["notices"]):
                alerts.append({"doi": row["doi"], "label": new["label"], "change": f"new notice {n}"})
            if new["published_version"] and not old.get("published_version"):
                alerts.append({"doi": row["doi"], "label": new["label"], "change": f"published version {new['published_version']}"})
            if new["contrasting"] > old.get("contrasting", 0):
                alerts.append({"doi": row["doi"], "label": new["label"],
                               "change": f"contrasting statements {old.get('contrasting', 0)} → {new['contrasting']}"})
        ctx.db.execute("UPDATE watch SET last_state=?, last_checked=? WHERE doi=?", (json.dumps(new), engine.now(), row["doi"]))
    return alerts
