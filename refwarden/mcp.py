"""Minimal MCP server over stdio (JSON-RPC 2.0, newline-delimited). No SDK needed.

In an agent harness the host model does the judging: `refwarden_judgement_packet` hands it the
items and instructions, and `refwarden_submit_judgements` stores its answers. No API key needed.
"""
import json
import sys
import traceback

from . import __version__, engine, export
from .parse import parse_reference
from .resolve import enrich, resolve

TOOLS = [
    {"name": "refwarden_audit_manuscript",
     "description": "Audit a manuscript's references and in-text citations: existence and accuracy against Crossref, "
                    "retractions and editorial notices, preprint supersession, how the literature cites each reference "
                    "(supporting/contrasting/mentioning), claim-to-source fit flags, and independence of co-cited sources. "
                    "Returns a finding ledger and a run_id.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "Full manuscript text including the reference list"},
         "title": {"type": "string"}, "contexts": {"type": "integer", "default": 50},
         "deep": {"type": "boolean", "default": False}}, "required": ["text"]}},
    {"name": "refwarden_check_crossrefs",
     "description": "Check a manuscript's internal cross-references offline: figures, tables, sections, appendices and equations "
                    "that are mentioned but missing, defined but never mentioned, out of order, misnumbered, or broken "
                    "(Word 'Error! Reference source not found.', LaTeX '??').",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "title": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "refwarden_paper_report",
     "description": "Citation report for one DOI: editorial notices and citing sentences classified supporting / contrasting / mentioning.",
     "inputSchema": {"type": "object", "properties": {"doi": {"type": "string"}, "contexts": {"type": "integer", "default": 100}},
                     "required": ["doi"]}},
    {"name": "refwarden_resolve_reference",
     "description": "Check one reference string against Crossref: verified, mismatch (with field differences), or not found.",
     "inputSchema": {"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]}},
    {"name": "refwarden_counter_evidence",
     "description": "Disconfirmation search: candidate papers whose own findings contradict or bound a claim.",
     "inputSchema": {"type": "object", "properties": {"claim": {"type": "string"}, "n": {"type": "integer", "default": 15}},
                     "required": ["claim"]}},
    {"name": "refwarden_judgement_packet",
     "description": "Get judgement items plus instructions for a run (kind 'claimfit' or 'stance'). Judge them yourself and "
                    "return the JSON with refwarden_submit_judgements.",
     "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"},
                                                      "kind": {"type": "string", "enum": ["claimfit", "stance"], "default": "claimfit"}},
                     "required": ["run_id"]}},
    {"name": "refwarden_submit_judgements",
     "description": "Store judgements for a run. role 'primary' fills missing verdicts; role 'validation' records an independent "
                    "second rating (use it only from a different session or model than the one that produced the primary verdicts).",
     "inputSchema": {"type": "object", "properties": {
         "run_id": {"type": "string"}, "judgements": {"type": "string", "description": "The JSON reply, {\"items\": [...]}"},
         "rater": {"type": "string", "description": "model id or harness name"},
         "role": {"type": "string", "enum": ["primary", "validation"], "default": "primary"}},
         "required": ["run_id", "judgements", "rater"]}},
    {"name": "refwarden_show_run",
     "description": "Render a stored run: format 'md' (ledger), 'author' (plain language), 'json'.",
     "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "format": {"type": "string", "default": "md"}},
                     "required": ["run_id"]}},
]


def call(ctx, name, a):
    if name == "refwarden_audit_manuscript":
        run = engine.audit(ctx, a["text"], a.get("title") or "manuscript", None, int(a.get("contexts", 50)), bool(a.get("deep")))
        return export.to_markdown(run) + f"\n\nrun_id: {run['run_id']}"
    if name == "refwarden_check_crossrefs":
        run = engine.crossref_check(ctx, a["text"], a.get("title") or "manuscript")
        return export.to_markdown(run) + f"\n\nrun_id: {run['run_id']}"
    if name == "refwarden_paper_report":
        run = engine.paper_report(ctx, a["doi"], int(a.get("contexts", 100)))
        return export.to_markdown(run) + f"\n\nrun_id: {run['run_id']}"
    if name == "refwarden_resolve_reference":
        ref = parse_reference(a["reference"], 1)
        res = resolve(ctx, ref)
        if res["work"]:
            enrich(ctx, res["work"], engine.coverage_block())
        return json.dumps(dict(ref, **res), indent=1, ensure_ascii=False, default=str)
    if name == "refwarden_counter_evidence":
        run = engine.counter_evidence(ctx, a["claim"], int(a.get("n", 15)))
        return export.to_markdown(run)
    if name == "refwarden_judgement_packet":
        return export.packet_markdown(export.packet(ctx, ctx.db.load_run(a["run_id"]), a.get("kind", "claimfit")))
    if name == "refwarden_submit_judgements":
        rep = export.apply_judgements(ctx, ctx.db.load_run(a["run_id"]), a["judgements"], a["rater"], "model", a.get("role", "primary"))
        return json.dumps(rep, indent=1)
    if name == "refwarden_show_run":
        fmt = a.get("format", "md")
        run = ctx.db.load_run(a["run_id"])
        if fmt == "json":
            return json.dumps(run, indent=1, default=str)
        return export.to_markdown(run, "author" if fmt == "author" else "internal")
    raise ValueError(f"unknown tool {name}")


def main(cfg):
    # JSON-RPC over stdio is UTF-8 with bare \n line ends on every platform (Windows would otherwise use cp1252 and \r\n)
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, ValueError):
        pass
    ctx = engine.Ctx(cfg, progress=lambda m: print(m, file=sys.stderr))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:          # notification
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "refwarden", "version": __version__}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = msg.get("params") or {}
                try:
                    text = call(ctx, p.get("name"), p.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": text}]}
                except Exception as e:
                    result = {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
                    traceback.print_exc(file=sys.stderr)
            elif method == "ping":
                result = {}
            else:
                raise KeyError(method)
            out = {"jsonrpc": "2.0", "id": mid, "result": result}
        except KeyError:
            out = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
