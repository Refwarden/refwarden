"""Renderers and the judgement-packet round trip.

Two output modes: INTERNAL (the ledger, IDs and codes) and AUTHOR (plain language, no
protocol vocabulary). An outward document never carries more certainty than its ledger row.

Judgement packets let any harness supply the model: export items + prompt, run them in any chat
or agent (Claude, ChatGPT, Gemini, a local model, a human), import the JSON reply.
"""
import csv
import datetime as dt
import io
import json

from . import engine
from .llm import extract_json, prompt
from .resolve import ref_label, work_label
from .textutil import quote_in

AUTHOR_CONF = {"Confirmed": "Verified", "Probable": "Our assessment, not confirmed", "Question": "Please check"}
AUTHOR_BAND = [("Fix before submission", ("Fatal", "Major")), ("Should fix", ("Moderate",)), ("If time allows", ("Minor",))]


def md_escape(s):
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def to_markdown(run, mode="internal"):
    if run["kind"] == "paper":
        return paper_markdown(run)
    if run["kind"] == "counter":
        return counter_markdown(run)
    cov = run["coverage"]
    if run["kind"] == "xrefs":
        return xrefs_markdown(run, mode)
    L = [f"# Reference audit — {run['title']}", ""]
    if mode == "author":
        L += [f"Checked {cov['references_parsed']} references and {cov['citations_found']} citing sentences against public "
              f"scholarly registries ({', '.join(cov['sources_used']) or 'none reachable'}), and the manuscript's "
              "internal cross-references to figures, tables, sections, appendices and equations.", ""]
        L += author_findings(run)
        L += ["## What this check did not cover", ""] + [f"- {n}" for n in cov["notes"]]
        L += [f"- Source not used: {k} ({v})" for k, v in cov["skipped"].items()]
        L += ["- Full texts were not read; source fit was judged on abstracts and registry metadata only.", ""]
        return "\n".join(L)
    s = run["summary"]
    L += [f"Run `{run['run_id']}` · {run['created_at']} · manuscript SHA-256 `{run.get('sha256', '')[:16]}…`",
          f"Passes {engine.pass_label(engine.PASS)} (references and citations) and {engine.pass_label(engine.XPASS)} (internal cross-references) · model: {cov['model']}", "",
          f"> {run['validation']}", "", "## Coverage", "",
          f"- References parsed: {cov['references_parsed']} · resolved: {cov['resolved']} · with abstract: {cov['with_abstract']}",
          f"- In-text citing sentences: {cov['citations_found']} ({cov['citation_style']}) · claim–source pairs: "
          f"{cov['claim_pairs']} · judged by a model: {cov['claim_pairs_judged']}",
          f"- Sources used: {', '.join(cov['sources_used']) or 'none'} · HTTP calls: {cov['http_calls']}"]
    L += [f"- Skipped: {k} — {v}" for k, v in cov["skipped"].items()]
    L += [f"- Note: {n}" for n in cov["notes"]]
    L += [f"- Error: {e}" for e in cov["errors"][:10]]
    if run.get("recheck"):
        L += ["", f"Recheck of `{run['parent_run']}` at {run['recheck']['checked_at']}: "
                  f"{len(run['recheck']['changes'])} change(s) in editorial status."]
    L += ["", "## Finding ledger", ""] + ledger_table(run["findings"])
    L += ["", "## References", "", "| # | Reference | Registry status | Notices | Citation statements (S / C / M) | Access |",
          "|---|---|---|---|---|---|"]
    for r in run["references"]:
        w = r.get("work") or {}
        t = (r.get("statements_summary") or {}).get("tallies")
        notes = ", ".join(n["label"] for n in w.get("notices") or []) or "—"
        sc = f"{t['supporting']} / {t['contrasting']} / {t['mentioning']}" if t else "—"
        doi = f"· {w['doi']}" if w.get("doi") else ""
        L.append(f"| {r['no']} | {md_escape(r['raw'][:110])} | {r['status']} {doi} | {md_escape(notes)} | {sc} | "
                 f"{w.get('access', '—')} |")
    if run["independence"]:
        L += ["", "## Independence of co-cited references", ""]
        for ind in run["independence"]:
            L.append(f"- {ind['cid']}: {len(ind['ref_nos'])} references → {ind['lines']} independent line(s); groups {ind['groups']}")
    L += xrefs_inventory(run.get("xrefs"))
    L += ["", "*Citation-statement stance is machine-classified (method shown per finding). Repetition across runs or "
              "models is concordance, not confirmation.*"]
    return "\n".join(L)


def author_findings(run):
    L = []
    for band, sevs in AUTHOR_BAND:
        rows = [f for f in run["findings"] if f["severity"] in sevs and f["status"] in ("Open", "Evidence-required")]
        if not rows:
            continue
        L += [f"## {band}", ""]
        for f in rows:
            L += [f"**{f['location']}** — {f['defect']}.", "",
                  f"- What we found: {f['evidence']}", f"- Why it matters: {f['consequence']}.",
                  f"- What to change: {f['min_repair']}.", f"- Status of this point: {AUTHOR_CONF[f['confidence']]}.", ""]
    return L


def ledger_table(findings):
    L = ["| ID | Pass | Location | Defect | Evidence | Consequence | Severity | Confidence | Minimum repair | Status |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for f in findings:
        L.append("| " + " | ".join(md_escape(f[k]) for k in ("id", "pass", "location", "defect", "evidence", "consequence",
                                                           "severity", "confidence", "min_repair", "status")) + " |")
    return L


def xrefs_inventory(x):
    if not x or not x["inventory"]:
        return []
    L = ["", "## Internal cross-references", "", "| Object | Defined in the document | Mentioned in the text |", "|---|---|---|"]
    for kind, v in x["inventory"].items():
        L.append(f"| {kind} | {', '.join(v['defined']) or '—'} | {', '.join(v['mentioned']) or '—'} |")
    return L


def xrefs_markdown(run, mode="internal"):
    L = [f"# Cross-reference check — {run['title']}", ""]
    if mode == "author":
        L += ["Checked the manuscript's references to its own figures, tables, sections, appendices and equations.", ""]
        L += author_findings(run) or ["No cross-reference problems found.", ""]
        L += [f"- {n}" for n in run["coverage"]["notes"]]
        return "\n".join(L)
    L += [f"Run `{run['run_id']}` · {run['created_at']} · rule-based, offline · pass {engine.pass_label(engine.XPASS)}", ""]
    L += [f"- Note: {n}" for n in run["coverage"]["notes"]]
    L += ["", "## Finding ledger", ""] + (ledger_table(run["findings"]) if run["findings"] else ["No findings."])
    L += xrefs_inventory(run["xrefs"])
    return "\n".join(L)


def paper_markdown(run):
    w, sm = run["work"], run["summary"]
    t = sm["tallies"]
    L = [f"# Citation report — {work_label(w)}", "", f"**{w.get('title')}** · {w.get('venue') or ''} · doi:{w.get('doi')}", "",
         f"> {run['validation']}", ""]
    if w.get("notices"):
        L += ["## Editorial notices", ""] + [f"- **{n['label']}** {n.get('doi') or ''} ({n.get('date') or ''}, {n['source']})"
                                               for n in w["notices"]] + [""]
    L += ["## How it is cited", "",
          f"{t['total']} citation statements from {sm['citing_papers']} citing papers: "
          f"**{t['supporting']} supporting · {t['contrasting']} contrasting · {t['mentioning']} mentioning** "
          f"(method: {', '.join(t['methods']) or 'n/a'}). Influential citations: {sm['influential']}.", ""]
    if sm["sections"]:
        L.append("Sections: " + ", ".join(f"{k} {v}" for k, v in sorted(sm["sections"].items(), key=lambda x: -x[1])))
    if sm["intents"]:
        L.append("Intents (Semantic Scholar): " + ", ".join(f"{k} {v}" for k, v in sm["intents"].items()))
    for label in ("contrasting", "supporting"):
        rows = [s for s in run["statements"] if s["stance"] == label][:15]
        if rows:
            L += ["", f"## {label.title()} statements", ""]
            for s in rows:
                L.append(f"- \"{s['context'][:400]}\" — {s.get('citing_title') or '?'} ({s.get('citing_year') or 'n.d.'}"
                         f"{', doi:' + s['citing_doi'] if s.get('citing_doi') else ''}) · {s['stance_method']}"
                         f"{' · ' + s['section'] if s.get('section') else ''}")
    L += ["", "## Coverage", "", f"- Sources: {', '.join(run['coverage']['sources_used'])}; model: {run['coverage']['model']}"]
    L += [f"- Skipped: {k} — {v}" for k, v in run["coverage"]["skipped"].items()]
    L += ["- Contexts come from Semantic Scholar (publisher-restricted contexts are absent) and, in deep mode, "
          "open-access full text in Europe PMC. Counts describe the retrieved sample, not all citing literature."]
    return "\n".join(L)


def counter_markdown(run):
    L = [f"# Disconfirmation screen", "", f"**Claim:** {run['claim']}", "", f"> {run['validation']}", "",
         "| Relation | Paper | Evidence (verbatim from abstract) | Method |", "|---|---|---|---|"]
    for c in run["candidates"]:
        if not c["relation"] or c["relation"] in ("unrelated",):
            continue
        q = md_escape(c["quote"][:300]) + ("" if c.get("quote_verified", c["method"] == "heuristic") else " ⚠ quote not verbatim")
        L.append(f"| {c['relation']} | {md_escape(c['title'])} ({c.get('year') or 'n.d.'}) {c.get('doi') or ''} | {q} | {c['method']} |")
    L += ["", "Relations ending in '?' come from cue-phrase screening and need reading. " + " ".join(run["coverage"]["notes"])]
    return "\n".join(L)


def to_csv(run):
    buf = io.StringIO()
    cols = ["id", "pass", "location", "defect", "evidence", "consequence", "severity", "confidence", "min_repair", "status",
            "check", "method"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for f in run.get("findings", []):
        w.writerow(f)
    return buf.getvalue()


# --- judgement packets ---------------------------------------------------------

def packet(ctx, run, kind="claimfit", blind=True):
    """Items + instructions for any harness. `blind` hides the tool's own verdicts (for validation)."""
    if kind == "claimfit":
        works = {r["no"]: r.get("work") or {} for r in run["references"]}
        items = []
        for k in run["claimfit"]:
            w = works[k["ref_no"]]
            if not w.get("abstract"):
                continue
            it = {"id": k["kid"], "claim": engine.strip_markers(k["claim"]),
                  "source": {"title": w.get("title"), "year": w.get("year"), "venue": w.get("venue"), "doi": w.get("doi")},
                  "abstract": w["abstract"][:4000]}
            if not blind and k.get("verdict"):
                it["tool_verdict"] = k["verdict"]
            items.append(it)
        header = "Judge each item independently."
    elif kind == "stance":
        dois = [r["work"]["doi"] for r in run.get("references", []) if (r.get("work") or {}).get("doi")] or \
               [run.get("work", {}).get("doi")]
        items = []
        for d in dois:
            rows = ctx.db.query("SELECT id, context, cited_doi FROM statements WHERE cited_doi=? LIMIT 200", (d,))
            items += [{"id": f"S{r['id']}", "target_doi": r["cited_doi"], "context": r["context"][:1200]} for r in rows]
        header = "Each item names its TARGET by DOI (target_doi); judge the stance toward that target."
    else:
        raise ValueError("kind must be claimfit or stance")
    return {"run_id": run["run_id"], "kind": kind, "blind": blind, "instructions": prompt("stance" if kind == "stance" else kind),
            "header": header, "items": items}


def packet_markdown(p):
    return (p["instructions"] + f"\n\n---\nRun: {p['run_id']} · kind: {p['kind']} · {len(p['items'])} items\n\n"
            + p["header"] + "\n\nITEMS:\n```json\n" + json.dumps(p["items"], ensure_ascii=False, indent=1) + "\n```\n")


def parse_reply(data):
    if isinstance(data, (dict, list)):
        obj = data
    else:
        text = data.strip()
        try:
            obj = [json.loads(l) for l in text.splitlines() if l.strip()] if text.startswith("{") and "\n{" in text else extract_json(text)
        except ValueError:
            obj = extract_json(text)
    rows = obj.get("items", []) if isinstance(obj, dict) else obj
    return [r for r in rows if isinstance(r, dict) and r.get("id") is not None]


def apply_judgements(ctx, run, data, rater, rater_kind="model", role="primary"):
    """Import judgements from any harness.

    role=primary     fills verdicts the run does not have yet (e.g. the tool ran without a model)
    role=validation  records an independent second rating; never overwrites the primary verdict.
                     A human rating that agrees and quotes the abstract verbatim upgrades the finding to
                     Confirmed; a model rating that agrees is recorded as concordance and changes nothing.
    """
    rows = parse_reply(data)
    stamp = engine.now()
    report = {"received": len(rows), "applied": 0, "agree": 0, "disagree": [], "unknown_ids": []}
    if rows and "stance" in rows[0]:
        for r in rows:
            sid = str(r["id"]).lstrip("S")
            if r.get("stance") not in ("supporting", "contrasting", "mentioning") or not sid.isdigit():
                report["unknown_ids"].append(r["id"])
                continue
            if role == "primary":
                ctx.db.execute("UPDATE statements SET stance=?, stance_method=?, stance_note=? WHERE id=?",
                               (r["stance"], f"harness:{rater}", r.get("why", ""), int(sid)))
            ctx.db.execute("INSERT INTO ratings(run_id, item_id, rater, rater_kind, role, verdict, quote, note, created_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?)", (run["run_id"], r["id"], rater, rater_kind, role, r["stance"], "", r.get("why", ""), stamp))
            report["applied"] += 1
        if role == "primary":
            refresh_statement_summaries(ctx, run)
    else:
        items = {k["kid"]: k for k in run.get("claimfit", [])}
        works = {r["no"]: r.get("work") or {} for r in run.get("references", [])}
        for r in rows:
            k = items.get(str(r["id"]))
            if not k or r.get("verdict") not in engine.VERDICTS:
                report["unknown_ids"].append(r["id"])
                continue
            w = works[k["ref_no"]]
            ctx.db.execute("INSERT INTO ratings(run_id, item_id, rater, rater_kind, role, verdict, quote, note, created_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?)", (run["run_id"], k["kid"], rater, rater_kind, role, r["verdict"],
                                                          r.get("quote", ""), r.get("overclaim", ""), stamp))
            report["applied"] += 1
            if role == "primary" and not k.get("verdict"):
                engine.apply_judgement(k, r, w, f"harness:{rater}")
            elif role == "validation":
                agree = r["verdict"] == k.get("verdict")
                verified = quote_in(r.get("quote") or "", w.get("abstract") or "")
                k.setdefault("validations", []).append({"rater": rater, "kind": rater_kind, "verdict": r["verdict"],
                                                        "agree": agree, "quote_verified": verified})
                if agree:
                    report["agree"] += 1
                    if rater_kind == "human" and verified:
                        k["human_confirmed"] = rater
                else:
                    report["disagree"].append({"id": k["kid"], "tool": k.get("verdict"), rater: r["verdict"]})
    run.setdefault("judgement_log", []).append({"at": stamp, "rater": rater, "kind": rater_kind, "role": role,
                                                "applied": report["applied"]})
    old = run.get("findings", [])
    run["findings"] = engine.carry_status(old, engine.build_findings(run))
    for f in run["findings"]:
        k = next((x for x in run.get("claimfit", []) if x["kid"] == f.get("item")), None)
        if k and k.get("human_confirmed") and f["check"] == "claim-fit" and k.get("verdict"):
            f["confidence"] = "Confirmed"
            f["evidence"] += f" [validated by {k['human_confirmed']} against the abstract]"
        elif k and any(not v["agree"] for v in k.get("validations", [])):
            f["evidence"] += " [a validation rater disagrees — adjudicate]"
    run["coverage"]["claim_pairs_judged"] = sum(1 for i in run.get("claimfit", []) if i.get("verdict"))
    run["summary"] = engine.summarize_run(run) if run["kind"] == "audit" else run.get("summary")
    ctx.db.save_run(run)
    return report


def refresh_statement_summaries(ctx, run):
    from .stance import tally  # noqa: F401  (summaries recomputed from stored rows)
    refs = run.get("references") or []
    for r in refs:
        d = (r.get("work") or {}).get("doi")
        if not d:
            continue
        rows = ctx.db.query("SELECT * FROM statements WHERE cited_doi=?", (d,))
        for x in rows:
            x["intents"] = json.loads(x["intents"] or "[]")
        if rows:
            r["statements_summary"] = engine.summarize_statements(rows)
            r["top_contrasting"] = [x for x in rows if x["stance"] == "contrasting"][:5]
