"""`refwarden selftest`: one command that tests an installation and writes a report to send back.

Runs the offline test suite, probes every data source, checks known-answer cases against the live
registries, exercises the MCP server, and saves:
  selftest-report.md      what passed, what failed, and why (no manuscript content)
  selftest-responses.json the raw public registry answers seen during the run, so they can become
                          test fixtures recorded from the real services rather than from documentation
Everything uses a fresh database inside the output folder, so your own runs are not touched.
"""
import contextlib
import io
import json
import locale
import os
import platform
import subprocess
import sys
import time
import unittest

from . import __version__, engine, xrefs
from .config import load_config
from .db import DB
from .parse import parse_reference
from .resolve import enrich, resolve
from .sources import crossref, europepmc, openalex, s2

PKG = os.path.dirname(__file__)
REPO = os.path.dirname(PKG)

# Known answers. Public papers chosen because their registry facts are stable and well documented.
REPRO = "10.1126/science.aac4716"      # Open Science Collaboration (2015), Science
RETRACTED = "10.1016/s0140-6736(97)11096-0"   # Wakefield et al. (1998), Lancet; retracted 2010
CASES = [
    ("correct reference resolves",
     "Open Science Collaboration (2015). Estimating the reproducibility of psychological science. Science, 349(6251), aac4716.",
     lambda r: r["status"] == "verified", "status verified"),
    ("wrong year is caught",
     "Open Science Collaboration (2013). Estimating the reproducibility of psychological science. Science, 349(6251), aac4716.",
     lambda r: r["status"] == "mismatch" and any(d["field"] == "year" for d in r["match"].get("diffs", [])),
     "status mismatch, year differs"),
    ("invented reference is not found",
     "Dimitrov, P. (2023). Hallucinated citations in large language model outputs: A registry-based audit. Journal of Imaginary Results, 1, 1-10.",
     lambda r: r["status"] == "not_found", "status not_found"),
    ("retracted paper is flagged",
     f"Wakefield, A. J., et al. (1998). Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children. The Lancet, 351, 637-641. https://doi.org/{RETRACTED}",
     lambda r: bool((r.get("work") or {}).get("is_retracted")), "work marked retracted"),
]


class Report:
    def __init__(self):
        self.rows, self.lines = [], []

    def check(self, area, name, ok, detail, expected=""):
        status = {True: "PASS", False: "FAIL", None: "CHECK"}[ok]
        self.rows.append((area, name, status, expected, str(detail)[:300].replace("\n", " ").replace("|", "/")))
        print(f"  [{status:5}] {area}: {name} — {str(detail)[:120]}")


def _timed(fn):
    t = time.time()
    try:
        return fn(), None, time.time() - t
    except Exception as e:  # noqa: BLE001 - every failure is reported, none is fatal
        return None, f"{type(e).__name__}: {e}", time.time() - t


def run_unit_tests(rep):
    tests = os.path.join(REPO, "tests")
    if not os.path.isdir(tests):
        rep.check("offline", "unit tests", None, "tests folder not found (installed without the repository)")
        return
    buf = io.StringIO()
    from . import http as H
    saved = (dict(H.HOST_INTERVAL), H.DEFAULT_INTERVAL)   # the tests switch throttling off; live calls need it back
    old = sys.path[:]
    sys.path[:0] = [REPO, tests]
    try:
        suite = unittest.defaultTestLoader.discover(tests, top_level_dir=tests)
        with contextlib.redirect_stderr(buf):
            res = unittest.TextTestRunner(stream=buf, verbosity=1).run(suite)
    finally:
        sys.path[:] = old
        H.HOST_INTERVAL.clear()
        H.HOST_INTERVAL.update(saved[0])
        H.DEFAULT_INTERVAL = saved[1]
    detail = f"{res.testsRun} run, {len(res.failures)} failed, {len(res.errors)} errors"
    if not res.wasSuccessful():
        detail += " — " + " | ".join(t.id() for t, _ in (res.failures + res.errors)[:5])
    rep.check("offline", "unit tests", res.wasSuccessful(), detail, "all pass")
    return buf.getvalue()


def run(out_dir, transport=None, quick=False):
    os.makedirs(out_dir, exist_ok=True)
    db_path = os.path.join(out_dir, "selftest.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg = load_config(db_path=db_path)
    ctx = engine.Ctx(cfg, db=DB(db_path), transport=transport)
    rep = Report()
    print(f"refwarden {__version__} selftest → {out_dir}")

    # 1. environment
    env = {"refwarden": __version__, "python": sys.version.split()[0], "platform": platform.platform(),
           "stdout_encoding": getattr(sys.stdout, "encoding", "?"), "locale": locale.getpreferredencoding(False),
           "mailto_set": bool(cfg.mailto), "openalex_key": bool(cfg.openalex_key), "s2_key": bool(cfg.s2_key),
           "model": ctx.llm.label or "none"}

    # 2. offline: unit tests, cross-references on the bundled sample, MCP handshake
    unit_log = run_unit_tests(rep) if not quick else None
    sample = os.path.join(REPO, "examples", "sample_manuscript.md")
    if os.path.exists(sample):
        with open(sample, encoding="utf-8") as f:
            x = xrefs.analyze(f.read())
        rep.check("offline", "cross-reference check on sample", len(x["issues"]) == 7, f"{len(x['issues'])} issues", "7 issues")
    msgs = "\n".join(json.dumps(m) for m in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})) + "\n"
    out, err, _ = _timed(lambda: subprocess.run([sys.executable, "-m", "refwarden", "--db", db_path, "mcp"], input=msgs,
                                                capture_output=True, text=True, encoding="utf-8", timeout=60).stdout)
    n_tools = len(json.loads(out.splitlines()[1])["result"]["tools"]) if out and len(out.splitlines()) > 1 else 0
    rep.check("offline", "MCP server starts and lists tools", n_tools >= 8, err or f"{n_tools} tools", "8 tools")

    # 3. live sources, one probe each
    probes = (("crossref", lambda: crossref.work(ctx, REPRO)), ("semanticscholar", lambda: s2.paper(ctx, REPRO)),
              ("europepmc", lambda: europepmc.find(ctx, "10.1038/nature12373")))
    for name, fn in probes:
        r, err, secs = _timed(fn)
        rep.check("sources", name, bool(r) and not err, err or (f"ok in {secs:.1f}s" if r else "no record returned"), "record returned")
    if openalex.enabled(ctx):
        r, err, _ = _timed(lambda: openalex.work_by_doi(ctx, REPRO))
        rep.check("sources", "openalex", bool(r) and not err, err or ("ok" if r else "no record"), "record returned")
    else:
        rep.check("sources", "openalex", None, "skipped: no OPENALEX_API_KEY (optional)")

    # 4. known-answer reference cases
    for name, text, test, expected in CASES:
        def one():
            ref = parse_reference(text, 1)
            res = resolve(ctx, ref)
            if res["work"]:
                enrich(ctx, res["work"], engine.coverage_block())
            return res
        r, err, _ = _timed(one)
        if err:
            rep.check("known answers", name, False, err, expected)
        else:
            w = r.get("work") or {}
            rep.check("known answers", name, test(r), f"status={r['status']} diffs={r['match'].get('diffs', [])} "
                      f"doi={w.get('doi')} notices={[n['type'] for n in w.get('notices') or []]}", expected)

    # 5. citation statements and counter-evidence (shape checks; counts vary over time)
    if not quick:
        r, err, secs = _timed(lambda: engine.paper_report(ctx, REPRO, contexts=40))
        if err:
            rep.check("statements", "citing sentences for a highly cited paper", False, err, "> 0 statements")
        else:
            t = r["summary"]["tallies"]
            rep.check("statements", "citing sentences for a highly cited paper", t["total"] > 0,
                      f"{t['total']} statements ({t['supporting']} S / {t['contrasting']} C / {t['mentioning']} M) "
                      f"from {r['summary']['citing_papers']} papers in {secs:.0f}s", "> 0 statements")
        r, err, _ = _timed(lambda: engine.counter_evidence(ctx, "Most published psychology findings replicate", n=5))
        rep.check("statements", "counter-evidence search returns candidates", None if not err else False,
                  err or f"{len(r['candidates'])} candidates; first: {(r['candidates'] or [{}])[0].get('title')}",
                  "some candidates (judge by eye)")

    # 6. write the report and the recorded responses
    errors = [f"{u} → {s}" for u, s in ctx.http.errors]
    L = [f"# refwarden selftest — {time.strftime('%Y-%m-%d %H:%M')}", "", "## Environment", ""]
    L += [f"- {k}: {v}" for k, v in env.items()]
    L += ["", "## Results", "", "| Area | Check | Result | Expected | Observed |", "|---|---|---|---|---|"]
    L += [f"| {a} | {n} | {s} | {e} | {d} |" for a, n, s, e, d in rep.rows]
    L += ["", f"HTTP calls: {ctx.http.calls}. HTTP errors: {len(errors)}"] + [f"- {e}" for e in errors[:20]]
    counts = {s: sum(1 for r in rep.rows if r[2] == s) for s in ("PASS", "FAIL", "CHECK")}
    L += ["", f"**Summary: {counts['PASS']} pass, {counts['FAIL']} fail, {counts['CHECK']} to check by eye.**",
          "", "This report contains no manuscript text. Send it, and selftest-responses.json, to whoever maintains your refwarden."]
    if unit_log and counts["FAIL"]:
        L += ["", "## Unit test log (tail)", "", "```", unit_log[-3000:], "```"]
    with open(os.path.join(out_dir, "selftest-report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    recorded = [{"key": r["key"], "status": r["status"], "body": (r["body"] or b"").decode("utf-8", "replace")}
                for r in ctx.db.query("SELECT key, status, body FROM http_cache")]
    with open(os.path.join(out_dir, "selftest-responses.json"), "w", encoding="utf-8") as f:
        json.dump(recorded, f, ensure_ascii=False)
    print(f"\n{counts['PASS']} pass · {counts['FAIL']} fail · {counts['CHECK']} check by eye")
    print(f"report: {os.path.join(out_dir, 'selftest-report.md')}")
    return counts, rep.rows
