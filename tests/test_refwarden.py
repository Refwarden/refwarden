"""Offline tests. Run: python -m unittest discover -s tests"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import unittest
import urllib.request

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from refwarden import engine, export, http as H, parse, stance  # noqa: E402
from refwarden.config import load_config  # noqa: E402
from refwarden.db import DB  # noqa: E402
from refwarden.sources import europepmc  # noqa: E402
from refwarden.textutil import quote_in, surname_key  # noqa: E402
from fakes import JATS, Fake  # noqa: E402

H.HOST_INTERVAL.clear()
H.DEFAULT_INTERVAL = 0
SAMPLE = open(os.path.join(ROOT, "examples", "sample_manuscript.md"), encoding="utf-8").read()


def make_ctx(llm_reply=None, provider="none"):
    cfg = load_config(db_path=":memory:", llm_provider=provider, llm_api_key="k" if provider != "none" else "",
                      llm_model="test-model" if provider != "none" else "", openalex_key="", mailto="t@example.org")
    return engine.Ctx(cfg, db=DB(":memory:"), transport=Fake(llm_reply))


def by_check(run, check):
    return [f for f in run["findings"] if f["check"] == check]


class TestParsing(unittest.TestCase):
    def test_surname_keys(self):
        self.assertEqual(surname_key("Smith, J."), "smith")
        self.assertEqual(surname_key("J. Smith, K. Lee"), "smith")
        self.assertEqual(surname_key("Smith JK, Lee K."), "smith")
        self.assertEqual(surname_key("van Dijk, T."), "dijk")
        self.assertEqual(surname_key("García Márquez, G."), "marquez")
        self.assertEqual(surname_key("Berg & Ncube"), "berg")

    def test_author_year(self):
        p = parse.parse_manuscript(SAMPLE)
        self.assertEqual(len(p["references"]), 10)
        self.assertEqual(p["style"], "author-year")
        c = next(c for c in p["citations"] if "Several studies" in c["sentence"])
        self.assertEqual(c["ref_nos"], [2, 3, 4])
        self.assertTrue(any(c["dangling"] for c in p["citations"]))

    def test_numeric(self):
        text = ("Intro\n\nAutomation bias is common [1, 3]. Fatigue matters [2-3].\n\nReferences\n"
                "[1] Berg K. Automation bias. Rev Sci. 2020.\n[2] Okafor A. Fatigue. QP. 2018.\n[3] Chen L. Reliance. ML. 2019.\n")
        p = parse.parse_manuscript(text)
        self.assertEqual(p["style"], "numeric")
        self.assertEqual([c["ref_nos"] for c in p["citations"]], [[1, 3], [2, 3]])

    def test_bibtex(self):
        bib = "@article{k1,\n author = {Berg, K. and Ncube, T.},\n title = {Automation bias},\n year = {2020},\n doi = {10.5555/rs.2020.007}\n}\n"
        refs = parse.parse_bibtex(bib)
        self.assertEqual((refs[0]["surname"], refs[0]["year"], refs[0]["doi_in_text"]), ("berg", 2020, "10.5555/rs.2020.007"))


class TestStance(unittest.TestCase):
    def test_heuristic(self):
        self.assertEqual(stance.heuristic("In contrast to Smith (2020), we find no effect.")[0], "contrasting")
        self.assertEqual(stance.heuristic("Our result is consistent with Smith (2020).")[0], "supporting")
        self.assertEqual(stance.heuristic("These data are not consistent with earlier work.")[0], "contrasting")
        self.assertEqual(stance.heuristic("We follow the method of Smith (2020).")[0], "mentioning")

    def test_live_trial_sentences(self):
        """Real citing sentences from the 2026-11-04 live trial that the first rules got wrong."""
        mentioning = [
            "…and small cohorts routinely inflate effect sizes that fail to replicate (Button et al., 2013; Poldrack et al., 2017).",
            "The deeper challenge is that these findings have not consistently yielded reproducible mappings between variation in neural measures and clinically meaningful variation 12,15–17.",
            "It likely favored lower-support-needs, digitally literate participants and does not support population estimates or generalization to all autistic people [8].",
            "Se esse tamanho for inviável, é preferível declarar o estudo como exploratório a apresentar resultados de um estudo subdimensionado como confirmatórios (Button et al., 2013).",
            "Given our small sample and the number of comparisons performed, the heel-breadth finding should be interpreted cautiously and requires replication [40].",
            "These results are based on two-sided Welch's t-tests, and are corroborated using Kolmogorov–Smirnov and Wilcoxon signed-rank tests.",
            "Against the backdrop of a widely diagnosed replication crisis in the social sciences (Dienlin et al., 2021; Open Science Collaboration, 2015), open science is regarded as a key corrective framework.",
            "Open Science Collaboration (2015) attempted to replicate one hundred studies published in 2008 in three high-ranking psychology journals.",
            "Numerous studies have shown that replications of findings from psychology and economics do not support the original conclusions at similar or higher rates than what we observe (28, 29, 39, 58).",
        ]
        for text in mentioning:
            with self.subTest(text[:50]):
                self.assertEqual(stance.heuristic(text)[0], "mentioning", stance.heuristic(text))
        self.assertEqual(stance.heuristic("We failed to replicate the original effect reported by Smith (2020).")[0], "contrasting")
        self.assertEqual(stance.heuristic("Our estimates replicate the earlier findings of Smith (2020).")[0], "supporting")

    def test_jats_contexts(self):
        got = europepmc.contexts_from_jats(JATS, {"doi": "10.5555/jsw.2021.001", "title": "AI assistance and review speed"})
        self.assertEqual([g["section"] for g in got], ["introduction", "discussion"])
        self.assertTrue(got[1]["context"].startswith("Unlike earlier reports"))
        self.assertNotIn("Unrelated", " ".join(g["context"] for g in got))


class TestAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = make_ctx()
        cls.audit_run = engine.audit(cls.ctx, SAMPLE, "sample", contexts=50)
        cls.refs = {r["no"]: r for r in cls.audit_run["references"]}

    def test_statuses(self):
        s = {n: r["status"] for n, r in self.refs.items()}
        self.assertEqual(s[1], "verified")
        self.assertEqual(s[5], "mismatch")
        self.assertEqual(s[6], "not_found")
        self.assertEqual(s[7], "doi_mismatch")

    def test_planted_defects_found(self):
        checks = {f["check"] for f in self.audit_run["findings"]}
        for c in ("existence", "accuracy", "retraction", "supersession", "contested", "calibration",
                  "independence", "claim-fit", "dangling", "uncited"):
            self.assertIn(c, checks)
        retr = by_check(self.audit_run, "retraction")[0]
        self.assertEqual((retr["severity"], retr["confidence"]), ("Major", "Confirmed"))
        self.assertIn("retraction-watch", retr["evidence"])

    def test_pass_relabelling(self):
        os.environ["REFWARDEN_PASS_LABELS"] = "REF=R-1/PR1,XREF=X-1"
        try:
            out = engine.number_findings([dict(f) for f in self.audit_run["findings"]])
        finally:
            del os.environ["REFWARDEN_PASS_LABELS"]
        self.assertTrue({f["pass"] for f in out} <= {"R-1", "X-1"})
        self.assertTrue(all(f["id"].startswith(("PR1-", "X1-")) for f in out))

    def test_contract_shape(self):
        counters = {}
        for f in self.audit_run["findings"]:
            tag = f["pass"]
            counters[tag] = counters.get(tag, 0) + 1
            self.assertEqual(f["id"], f"{tag}-{counters[tag]:02d}")
            for k in ("location", "defect", "evidence", "consequence", "min_repair"):
                self.assertTrue(f[k], (f["id"], k))
            self.assertIn(f["severity"], ("Fatal", "Major", "Moderate", "Minor"))
            self.assertIn(f["confidence"], ("Confirmed", "Probable", "Question"))

    def test_machine_judgement_never_confirmed(self):
        for f in self.audit_run["findings"]:
            if f["method"] not in ("metadata", "manuscript"):
                self.assertNotEqual(f["confidence"], "Confirmed", f)

    def test_independence(self):
        ind = next(i for i in self.audit_run["independence"] if len(i["ref_nos"]) == 3)
        self.assertEqual(ind["lines"], 2)
        f = by_check(self.audit_run, "independence")[0]
        self.assertEqual(f["confidence"], "Confirmed")     # shared ORCID + registry citation link

    def test_contested_tally(self):
        t = self.refs[1]["statements_summary"]["tallies"]
        self.assertEqual((t["contrasting"], t["supporting"]), (3, 1))

    def test_no_claimfit_on_wrong_work(self):
        self.assertFalse([k for k in self.audit_run["claimfit"] if k["ref_no"] in (6, 7)])

    def test_exports(self):
        md = export.to_markdown(self.audit_run)
        self.assertIn("| REF-01 |", md)
        author = export.to_markdown(self.audit_run, "author")
        self.assertNotIn("REF", author)
        self.assertNotIn("Probable", author)
        self.assertIn("Our assessment, not confirmed", author)
        self.assertTrue(export.to_csv(self.audit_run).startswith("id,pass,location"))

    def test_status_survives_recheck(self):
        run = json.loads(json.dumps(self.audit_run))
        run["findings"][0]["status"] = "Deferred"
        self.ctx.db.save_run(run)
        new = engine.recheck(self.ctx, run["run_id"])
        self.assertEqual(new["parent_run"], run["run_id"])
        self.assertEqual(new["findings"][0]["status"], "Deferred")


class TestCrossRefs(unittest.TestCase):
    TEXT = """# Paper

## 1 Introduction

As Figure 2 shows, review slows. Figure 1 and figures 3a–3b show the pipeline. See Tables 1-2, Table 4 and Section 2.1.
Details are in Appendix C, Eq. (2) and Section 7. Error! Reference source not found. As in Figure ?? too.

## 2 Methods

### 2.1 Data

The model is
y = a + b x (1)

Figure 1. Pipeline.

Figure 3: Panels.

**Table 1.** Turnaround.

Table 2. Robustness.

Table 2. Duplicate.

## References

Smith, J. (2020). Table 9 and Figure 8 of everything. Journal.

## Appendix A: Coding scheme

Table A1. Codes.
"""

    def issues(self):
        from refwarden import xrefs
        return {(i["type"], i["kind"], tuple(i["labels"])) for i in xrefs.analyze(self.TEXT)["issues"]}

    def test_detects_each_defect(self):
        got = self.issues()
        for expected in [("missing", "figure", ("2",)), ("gap", "figure", ("2",)), ("order", "figure", ("1",)),
                         ("missing", "table", ("4",)), ("duplicate", "table", ("2",)), ("unmentioned", "table", ("A1",)),
                         ("missing", "section", ("7",)), ("missing", "appendix", ("C",)), ("missing", "equation", ("2",)),
                         ("word-field-error", "field", ("Error! Reference source not found.",)),
                         ("latex-unresolved", "field", ("Figure ??",))]:
            self.assertIn(expected, got)

    def test_no_false_positives(self):
        got = self.issues()
        self.assertNotIn(("unmentioned", "figure", ("3",)), got)     # panels 3a-3b count as Figure 3
        self.assertNotIn(("unmentioned", "table", ("2",)), got)      # range 'Tables 1-2'
        self.assertNotIn(("missing", "section", ("2.1",)), got)
        self.assertFalse([i for i in got if i[2] in (("9",), ("8",))])  # numbers inside the reference list are ignored

    def test_docx(self):
        import io, zipfile
        from refwarden import xrefs
        W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        def para(text, style=None):
            ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
            return f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"
        body = (para("Results", "Heading1") + para("As shown in ") + para("Error! Reference source not found. and Table 2.")
                + para("Table 1: Turnaround.", "Caption"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")
        text = parse.load_text(buf.getvalue(), "draft.docx")
        self.assertIn("# Results", text)
        got = {(i["type"], tuple(i["labels"])) for i in xrefs.analyze(text)["issues"]}
        self.assertIn(("word-field-error", ("Error! Reference source not found.",)), got)
        self.assertIn(("missing", ("2",)), got)
        self.assertIn(("unmentioned", ("1",)), got)

    def test_docx_text_box_read_once(self):
        import io, zipfile
        from refwarden import xrefs
        ns = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
              'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"')
        box = '<w:txbxContent><w:p><w:r><w:t>Figure 1. Pipeline.</w:t></w:r></w:p></w:txbxContent>'
        anchor = (f'<w:p><w:r><w:t>See Figure 1.</w:t></w:r><w:r><mc:AlternateContent><mc:Choice>{box}</mc:Choice>'
                  f'<mc:Fallback>{box}</mc:Fallback></mc:AlternateContent></w:r></w:p>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", f"<w:document {ns}><w:body>{anchor}</w:body></w:document>")
        text = parse.load_text(buf.getvalue(), "boxed.docx")
        self.assertEqual(text.count("Figure 1. Pipeline."), 1)
        self.assertEqual(xrefs.analyze(text)["issues"], [])

    def test_in_audit_and_standalone(self):
        run = engine.audit(make_ctx(), SAMPLE, "sample", contexts=0)
        x = [f for f in run["findings"] if f["check"] == "cross-reference"]
        self.assertEqual(len(x), 7)
        self.assertTrue(all(f["id"].startswith("XREF-") and f["confidence"] in ("Confirmed", "Question") for f in x))
        solo = engine.crossref_check(make_ctx(), SAMPLE, "sample")
        self.assertEqual({f["defect"] for f in solo["findings"]}, {f["defect"] for f in x})
        self.assertIn("## Internal cross-references", export.to_markdown(solo))
        self.assertNotIn("XREF-", export.to_markdown(solo, "author"))


class TestJudgements(unittest.TestCase):
    def reply(self, body):
        items = json.loads(body["messages"][0]["content"].split("ITEMS:\n", 1)[1])
        out = []
        for it in items:
            if "context" in it:
                out.append({"id": it["id"], "stance": "mentioning", "why": "background"})
            else:
                quote = it["abstract"].split(". ")[0] if "fatigue" not in it["claim"] else "a paraphrase that is not in the abstract"
                out.append({"id": it["id"], "verdict": "overstated", "quote": quote, "strongest_supported": "An association.",
                            "overclaim": "causal"})
        return "Here you go:\n```json\n" + json.dumps({"items": out}) + "\n```"

    def test_llm_claimfit_quote_check(self):
        ctx = make_ctx(self.reply, provider="anthropic")
        run = engine.audit(ctx, SAMPLE, "sample", contexts=0)
        judged = [k for k in run["claimfit"] if k.get("verdict")]
        self.assertTrue(judged)
        okafor = next(k for k in judged if k["ref_no"] == 5)
        self.assertFalse(okafor["quote_verified"])
        f = next(f for f in by_check(run, "claim-fit") if f.get("item") == okafor["kid"] and f["method"].startswith("llm"))
        self.assertEqual(f["confidence"], "Question")          # unverifiable quote cannot be Probable
        other = next(k for k in judged if k["ref_no"] != 5)
        self.assertTrue(other["quote_verified"])

    def test_model_failure_falls_back_and_is_reported(self):
        ctx = make_ctx(None, provider="anthropic")          # fake endpoint answers 404
        run = engine.audit(ctx, SAMPLE, "sample", contexts=50)
        errs = " ".join(run["coverage"]["errors"])
        self.assertIn("model (claim-fit)", errs)
        self.assertIn("model (stance)", errs)
        self.assertEqual(run["references"][0]["statements_summary"]["tallies"]["methods"], ["heuristic"])

    def test_packet_round_trip_and_validation(self):
        ctx = make_ctx()
        run = engine.audit(ctx, SAMPLE, "sample", contexts=0)
        p = export.packet(ctx, run, "claimfit")
        self.assertTrue(p["items"])
        self.assertNotIn("tool_verdict", json.dumps(p["items"]))
        item = p["items"][0]
        quote = item["abstract"].split(". ")[0]
        primary = {"items": [{"id": item["id"], "verdict": "overstated", "quote": quote, "strongest_supported": "x"}]}
        rep = export.apply_judgements(ctx, run, json.dumps(primary), "model-A", "model", "primary")
        self.assertEqual(rep["applied"], 1)
        f = next(f for f in run["findings"] if f.get("item") == item["id"] and f["method"] == "harness:model-A")
        self.assertEqual(f["confidence"], "Probable")
        export.apply_judgements(ctx, run, json.dumps(primary), "model-B", "model", "validation")
        f = next(f for f in run["findings"] if f.get("item") == item["id"] and f["method"] == "harness:model-A")
        self.assertEqual(f["confidence"], "Probable")          # concordance is not confirmation
        export.apply_judgements(ctx, run, json.dumps(primary), "the author", "human", "validation")
        f = next(f for f in run["findings"] if f.get("item") == item["id"] and f["method"] == "harness:model-A")
        self.assertEqual(f["confidence"], "Confirmed")
        stored = ctx.db.load_run(run["run_id"])
        self.assertEqual(len(stored["judgement_log"]), 3)


class TestOtherJobs(unittest.TestCase):
    def test_paper_report(self):
        run = engine.paper_report(make_ctx(), "https://doi.org/10.5555/jsw.2021.001", contexts=50)
        self.assertEqual(run["summary"]["tallies"]["contrasting"], 3)
        self.assertIn("Contrasting statements", export.to_markdown(run))

    def test_counter(self):
        run = engine.counter_evidence(make_ctx(), "AI assistance speeds up peer review turnaround", n=5)
        self.assertEqual(run["candidates"][0]["relation"], "contradicts?")
        self.assertTrue(quote_in(run["candidates"][0]["quote"], "Across 60 journals, AI assistance in peer review showed no effect on review turnaround time."))


class TestLiveRecorded(unittest.TestCase):
    """Replays real Crossref / Semantic Scholar / Europe PMC answers recorded by `refwarden selftest`
    (tests/fixtures/live_responses.json), so these assertions rest on what the services actually return."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "fixtures", "live_responses.json"), encoding="utf-8") as f:
            cls.recorded = {r["key"]: (r["status"], r["body"].encode("utf-8")) for r in json.load(f)}

    def ctx(self):
        def replay(method, url, headers, body):
            status, payload = self.recorded.get(H.Http.cache_key(method, url, body), (404, b"{}"))
            return status, payload, {}
        cfg = load_config(db_path=":memory:", llm_provider="none", openalex_key="", mailto="")
        return engine.Ctx(cfg, db=DB(":memory:"), transport=replay)

    def resolve(self, text):
        from refwarden.resolve import enrich, resolve
        ctx = self.ctx()
        res = resolve(ctx, parse.parse_reference(text, 1))
        if res["work"]:
            enrich(ctx, res["work"], engine.coverage_block())
        return res

    def test_known_answers(self):
        from refwarden.selftest import CASES
        for name, text, check, expected in CASES:
            with self.subTest(name):
                self.assertTrue(check(self.resolve(text)), expected)

    def test_retraction_from_real_crossref_record(self):
        from refwarden.selftest import CASES
        w = self.resolve(CASES[3][1])["work"]
        retr = [n for n in w["notices"] if n["type"] == "retraction"]
        self.assertTrue(retr and retr[0]["doi"] and retr[0]["date"])
        self.assertTrue(w["is_retracted"])

    def test_real_citation_contexts(self):
        from refwarden.selftest import REPRO
        run = engine.paper_report(self.ctx(), REPRO, contexts=40)
        t = run["summary"]["tallies"]
        self.assertGreater(t["total"], 0)
        self.assertEqual(t["total"], t["supporting"] + t["contrasting"] + t["mentioning"])
        self.assertTrue(all(len(s["context"]) > 20 for s in run["statements"]))


class TestLiveTrialAudit(unittest.TestCase):
    """Full audit of examples/live_trial.md (real references, planted faults) replayed from the
    registry answers recorded during the 2026-11-04 live run."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "fixtures", "live_trial_responses.json"), encoding="utf-8") as f:
            recorded = {r["key"]: (r["status"], r["body"].encode("utf-8")) for r in json.load(f)}

        def replay(method, url, headers, body):
            status, payload = recorded.get(H.Http.cache_key(method, url, body), (404, b"{}"))
            return status, payload, {}
        cfg = load_config(db_path=":memory:", llm_provider="none", openalex_key="", mailto="")
        ctx = engine.Ctx(cfg, db=DB(":memory:"), transport=replay)
        with open(os.path.join(ROOT, "examples", "live_trial.md"), encoding="utf-8") as f:
            cls.run_ = engine.audit(ctx, f.read(), "live trial", contexts=30)
        cls.refs = {r["raw"].split(",")[0]: r for r in cls.run_["references"]}

    def defects(self, check):
        return [f for f in self.run_["findings"] if f["check"] == check]

    def test_planted_faults_found(self):
        self.assertEqual(self.refs["Camerer"]["status"], "doi_mismatch")
        self.assertEqual(self.refs["Dimitrov"]["status"], "not_found")
        self.assertEqual(self.refs["Simmons"]["status"], "mismatch")
        self.assertTrue(self.refs["LaCour"]["work"]["is_retracted"])
        self.assertTrue(any("different years" in f["defect"] for f in self.defects("accuracy")))
        self.assertTrue(self.defects("calibration"))
        ind = [f for f in self.defects("independence") if "1 independent line" in f["defect"]]
        self.assertTrue(ind)
        self.assertEqual(len(self.defects("cross-reference")), 2)

    def test_real_notices(self):
        notices = {n["type"] for r in self.run_["references"] for n in (r.get("work") or {}).get("notices", [])}
        self.assertTrue({"retraction", "erratum", "correction"} <= notices)

    def test_no_false_alarms_on_correct_claims(self):
        osc = [f for f in self.defects("claim-fit") if "Collaboration" in f["location"]]
        self.assertEqual(osc, [])                      # '36%' is in the abstract as 'Thirty-six percent'
        self.assertFalse([f for f in self.defects("uncited") if "Simmons" in f["location"]])


class TestInterfaces(unittest.TestCase):
    def test_mcp_handshake(self):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        env = dict(os.environ, REFWARDEN_DB=":memory:", REFWARDEN_HOME=os.path.join(HERE, "fixtures"))
        out = subprocess.run([sys.executable, "-m", "refwarden", "mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                             capture_output=True, text=True, cwd=ROOT, env=env, timeout=30).stdout.splitlines()
        self.assertEqual(len(out), 2)
        self.assertEqual(json.loads(out[0])["result"]["serverInfo"]["name"], "refwarden")
        names = [t["name"] for t in json.loads(out[1])["result"]["tools"]]
        self.assertIn("refwarden_audit_manuscript", names)
        self.assertIn("refwarden_check_crossrefs", names)

    def test_server(self):
        from http.server import ThreadingHTTPServer
        from refwarden.server import make_handler
        ctx = make_ctx()
        run = engine.audit(ctx, SAMPLE, "sample", contexts=0)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctx.cfg, ctx.db))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/") as resp:
                page = resp.read().decode()
            self.assertIn("refwarden", page)
            runs = json.loads(urllib.request.urlopen(base + "/api/runs").read())
            self.assertEqual(runs[0]["run_id"], run["run_id"])
            md = urllib.request.urlopen(f"{base}/api/runs/{run['run_id']}/export?fmt=author").read().decode()
            self.assertIn("Fix before submission", md)
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
