"""Command line. `python -m refwarden --help`"""
import argparse
import json
import os
import sys

from . import __version__, engine, export, watch
from .config import load_config
from .parse import load_text
from .sources import crossref, europepmc, openalex, s2


def ctx_from(args):
    cfg = load_config(db_path=getattr(args, "db", None))
    if getattr(args, "no_llm", False):
        cfg.llm_provider = "none"
    quiet = getattr(args, "quiet", False)
    return engine.Ctx(cfg, progress=None if quiet else (lambda m: print("·", m, file=sys.stderr)))


def emit(text, out=None):
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)


def render(run, fmt, ctx=None):
    if fmt == "json":
        return json.dumps(run, indent=1, ensure_ascii=False, default=str)
    if fmt == "csv":
        return export.to_csv(run)
    return export.to_markdown(run, "author" if fmt == "author" else "internal")


def cmd_audit(a):
    ctx = ctx_from(a)
    with open(a.file, "rb") as f:
        text = load_text(f.read(), a.file)
    bib = open(a.bib, encoding="utf-8").read() if a.bib else None
    run = engine.audit(ctx, text, a.title or os.path.basename(a.file), bib, a.contexts, a.deep)
    emit(render(run, a.format), a.out)
    print(f"run {run['run_id']} · {run['summary']}", file=sys.stderr)


def cmd_xrefs(a):
    with open(a.file, "rb") as f:
        text = load_text(f.read(), a.file)
    run = engine.crossref_check(ctx_from(a), text, a.title or os.path.basename(a.file))
    emit(render(run, a.format), a.out)
    print(f"run {run['run_id']} · {len(run['findings'])} finding(s)", file=sys.stderr)


def cmd_paper(a):
    run = engine.paper_report(ctx_from(a), a.doi, a.contexts, a.deep)
    emit(render(run, a.format), a.out)


def cmd_counter(a):
    run = engine.counter_evidence(ctx_from(a), a.claim, a.n)
    emit(render(run, a.format), a.out)


def cmd_resolve(a):
    from .parse import parse_reference
    from .resolve import enrich, resolve
    ctx = ctx_from(a)
    ref = parse_reference(a.reference, 1)
    res = resolve(ctx, ref)
    if res["work"]:
        enrich(ctx, res["work"], engine.coverage_block())
    print(json.dumps(dict(ref, **res), indent=1, ensure_ascii=False, default=str))


def cmd_runs(a):
    ctx = ctx_from(a)
    for r in ctx.db.list_runs(a.limit):
        print(f"{r['run_id']}  {r['kind']:<7} {r['created_at']}  {r['title'][:70]}" + (f"  (recheck of {r['parent_run']})" if r["parent_run"] else ""))


def load(ctx, run_id):
    run = ctx.db.load_run(run_id)
    if not run:
        sys.exit(f"no run {run_id}")
    return run


def cmd_show(a):
    ctx = ctx_from(a)
    emit(render(load(ctx, a.run_id), a.format), a.out)


def cmd_packet(a):
    ctx = ctx_from(a)
    p = export.packet(ctx, load(ctx, a.run_id), a.kind, blind=not a.with_verdicts)
    emit(json.dumps(p, indent=1, ensure_ascii=False) if a.json else export.packet_markdown(p), a.out)


def cmd_submit(a):
    ctx = ctx_from(a)
    run = load(ctx, a.run_id)
    with open(a.file, encoding="utf-8") as f:
        rep = export.apply_judgements(ctx, run, f.read(), a.rater, a.rater_kind, a.role)
    print(json.dumps(rep, indent=1))


def cmd_rejudge(a):
    run = engine.rejudge(ctx_from(a), a.run_id)
    c = run["coverage"]
    print(f"new run {run['run_id']} · claim-fit judged {c['claim_pairs_judged']}/{c['claim_pairs']}", file=sys.stderr)


def cmd_recheck(a):
    run = engine.recheck(ctx_from(a), a.run_id)
    print(json.dumps(run["recheck"], indent=1))
    print(f"new run {run['run_id']}", file=sys.stderr)


def cmd_status(a):
    ctx = ctx_from(a)
    ctx.db.execute("UPDATE findings SET status=? WHERE run_id=? AND finding_id=?", (a.status, a.run_id, a.finding_id))
    run = load(ctx, a.run_id)
    for f in run["findings"]:
        if f["id"] == a.finding_id:
            f["status"] = a.status
    ctx.db.save_run(run)
    print(f"{a.finding_id} → {a.status}")


def cmd_watch(a):
    ctx = ctx_from(a)
    if a.action == "add":
        for d in a.dois:
            watch.add(ctx, d)
        print(f"watching {len(a.dois)} DOI(s)")
    elif a.action == "list":
        for r in ctx.db.query("SELECT doi, label, last_checked FROM watch"):
            print(r["doi"], r["last_checked"] or "never checked")
    else:
        alerts = watch.run(ctx, a.contexts)
        print(json.dumps(alerts, indent=1, ensure_ascii=False) if alerts else "no changes")


def cmd_doctor(a):
    ctx = ctx_from(a)
    cfg = ctx.cfg
    print(f"refwarden {__version__} · db {cfg.db_path}")
    print(json.dumps(cfg.public(), indent=1))
    probe = "10.1038/nature12373"
    for name, fn in (("crossref", lambda: crossref.work(ctx, probe)), ("semanticscholar", lambda: s2.paper(ctx, probe)),
                     ("europepmc", lambda: europepmc.find(ctx, probe)),
                     ("openalex", lambda: openalex.work_by_doi(ctx, probe) if openalex.enabled(ctx) else "skipped (no key)")):
        try:
            r = fn()
            print(f"  {name:<16} {'ok' if r and r != 'skipped (no key)' else r or 'no record'}")
        except Exception as e:
            print(f"  {name:<16} FAILED: {e}")
    if ctx.llm.available:
        try:
            reply = ctx.llm.complete("Reply with the JSON object {\"ok\": true} and nothing else.", "ping", max_tokens=2000)
            print(f"  model {ctx.llm.label:<10} {reply.strip()[:60]}")
        except Exception as e:
            print(f"  model {ctx.llm.label} FAILED: {e}")
    else:
        print("  model            none — heuristics + judgement packets")


def cmd_selftest(a):
    from .selftest import run
    counts, _ = run(os.path.abspath(a.out), quick=a.quick)
    sys.exit(1 if counts["FAIL"] else 0)


def cmd_serve(a):
    from .server import serve
    serve(load_config(db_path=a.db), a.host, a.port)


def cmd_mcp(a):
    from .mcp import main
    main(load_config(db_path=a.db))


def utf8_streams():
    """Windows pipes and redirects default to the legacy code page; force UTF-8 so '→' or 'é' never crash output."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    utf8_streams()
    ap = argparse.ArgumentParser(prog="refwarden", description="Citation-context and reference-integrity checks.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--db", help="SQLite path (default ~/.refwarden/refwarden.db or $REFWARDEN_DB)")
    ap.add_argument("-q", "--quiet", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fmt = dict(choices=["md", "author", "json", "csv"], default="md")

    p = sub.add_parser("audit", help="audit a manuscript's references and citations")
    p.add_argument("file")
    p.add_argument("--bib", help="BibTeX file to use as the reference list")
    p.add_argument("--title")
    p.add_argument("--contexts", type=int, default=100, help="citing papers to sample per reference (0 = skip)")
    p.add_argument("--deep", action="store_true", help="add Europe PMC full-text contexts and second-order retraction check")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("-f", "--format", **fmt)
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("xrefs", help="check internal cross-references only (figures, tables, sections, appendices, equations); offline")
    p.add_argument("file")
    p.add_argument("--title")
    p.add_argument("-f", "--format", choices=["md", "author", "json", "csv"], default="md")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_xrefs)

    p = sub.add_parser("paper", help="citation report for one DOI (supporting / contrasting / mentioning)")
    p.add_argument("doi")
    p.add_argument("--contexts", type=int, default=200)
    p.add_argument("--deep", action="store_true")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("-f", "--format", **fmt)
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_paper)

    p = sub.add_parser("counter", help="disconfirmation search: find evidence against a claim")
    p.add_argument("claim")
    p.add_argument("-n", type=int, default=15)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("-f", "--format", **fmt)
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_counter)

    p = sub.add_parser("resolve", help="resolve one reference string")
    p.add_argument("reference")
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser("runs", help="list stored runs")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_runs)

    p = sub.add_parser("show", help="render a stored run")
    p.add_argument("run_id")
    p.add_argument("-f", "--format", **fmt)
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("packet", help="export judgement items + prompt for any AI harness or a human rater")
    p.add_argument("run_id")
    p.add_argument("--kind", choices=["claimfit", "stance"], default="claimfit")
    p.add_argument("--with-verdicts", action="store_true", help="include the tool's verdicts (default: blind)")
    p.add_argument("--json", action="store_true")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_packet)

    p = sub.add_parser("submit", help="import judgements returned by a harness or rater")
    p.add_argument("run_id")
    p.add_argument("file")
    p.add_argument("--rater", required=True, help="who judged: model id, harness, or person")
    p.add_argument("--rater-kind", choices=["model", "human"], default="model")
    p.add_argument("--role", choices=["primary", "validation"], default="primary")
    p.set_defaults(fn=cmd_submit)

    p = sub.add_parser("status", help="record the author's disposition of a finding")
    p.add_argument("run_id")
    p.add_argument("finding_id")
    p.add_argument("status", choices=["Open", "Accepted", "Disputed", "Deferred", "Evidence-required"])
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("rejudge", help="re-run the model on what a stored audit left unjudged (no new lookups)")
    p.add_argument("run_id")
    p.set_defaults(fn=cmd_rejudge)

    p = sub.add_parser("recheck", help="pre-submission sweep: re-query notices for a stored audit")
    p.add_argument("run_id")
    p.set_defaults(fn=cmd_recheck)

    p = sub.add_parser("watch", help="watchlist of DOIs you rely on")
    p.add_argument("action", choices=["add", "list", "run"])
    p.add_argument("dois", nargs="*")
    p.add_argument("--contexts", type=int, default=100)
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("doctor", help="check configuration and reachability of every source")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("selftest", help="test this installation end to end and write a report to send back")
    p.add_argument("--out", default="refwarden-selftest", help="folder for the report (default ./refwarden-selftest)")
    p.add_argument("--quick", action="store_true", help="skip unit tests and the slower live checks")
    p.set_defaults(fn=cmd_selftest)

    p = sub.add_parser("serve", help="browser UI + JSON API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("mcp", help="MCP server on stdio (Claude Code/Desktop, Cursor, Codex, Gemini CLI, ...)")
    p.set_defaults(fn=cmd_mcp)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
