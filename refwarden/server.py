"""Browser UI + JSON API on the standard library HTTP server.

Local by default (127.0.0.1). For a shared host set REFWARDEN_TOKEN; the API then requires
`Authorization: Bearer <token>` and the page asks for it once.
"""
import base64
import json
import os
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import __version__, engine, export
from .db import DB
from .parse import load_text

WEB = os.path.join(os.path.dirname(__file__), "web", "index.html")
JOBS = {}


def start_job(cfg, db, fn):
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "status": "running", "log": [], "run_id": None, "error": None}
    JOBS[jid] = job

    def progress(msg):
        job["log"].append(msg)
        del job["log"][:-200]

    def target():
        try:
            ctx = engine.Ctx(cfg, db=db, progress=progress)
            run = fn(ctx)
            job.update(status="done", run_id=run["run_id"])
        except Exception as e:
            job.update(status="error", error=str(e))
            progress(traceback.format_exc(limit=3))
    threading.Thread(target=target, daemon=True).start()
    return jid


def make_handler(cfg, db):
    class H(BaseHTTPRequestHandler):
        server_version = f"refwarden/{__version__}"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json", filename=None):
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, default=str)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
            self.send_header("Content-Length", str(len(data)))
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            if not cfg.token:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {cfg.token}"

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return {"_raw": raw.decode("utf-8", "replace")}

        def do_GET(self):
            u = urlsplit(self.path)
            parts = [p for p in u.path.split("/") if p]
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not parts:
                with open(WEB, "rb") as f:
                    return self._send(200, f.read(), "text/html")
            if parts[0] != "api":
                return self._send(404, {"error": "not found"})
            if not self._authorized():
                return self._send(401, {"error": "token required"})
            if parts[1:] == ["config"]:
                ctx = engine.Ctx(cfg, db=db)
                return self._send(200, {"version": __version__, "config": cfg.public(), "model": ctx.llm.label})
            if parts[1] == "jobs" and len(parts) == 3:
                return self._send(200, JOBS.get(parts[2]) or {"status": "unknown"})
            if parts[1:] == ["runs"]:
                return self._send(200, db.list_runs(100))
            if parts[1] == "runs" and len(parts) >= 3:
                run = db.load_run(parts[2])
                if not run:
                    return self._send(404, {"error": "no such run"})
                if len(parts) == 3:
                    return self._send(200, run)
                if parts[3] == "export":
                    fmt = q.get("fmt", "md")
                    ctx = engine.Ctx(cfg, db=db)
                    if fmt == "packet":
                        p = export.packet(ctx, run, q.get("kind", "claimfit"), blind=q.get("blind", "1") == "1")
                        return self._send(200, export.packet_markdown(p), "text/markdown", f"{run['run_id']}-packet.md")
                    body = {"json": lambda: json.dumps(run, indent=1, default=str), "csv": lambda: export.to_csv(run),
                            "author": lambda: export.to_markdown(run, "author")}.get(fmt, lambda: export.to_markdown(run))()
                    ext = {"json": "json", "csv": "csv"}.get(fmt, "md")
                    return self._send(200, body, "text/plain", f"{run['run_id']}-{fmt}.{ext}")
            if parts[1:] == ["statements"]:
                rows = db.query("SELECT * FROM statements WHERE cited_doi=? ORDER BY stance, citing_year DESC", (q.get("doi", ""),))
                return self._send(200, rows)
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            parts = [p for p in urlsplit(self.path).path.split("/") if p]
            if not parts or parts[0] != "api":
                return self._send(404, {"error": "not found"})
            if not self._authorized():
                return self._send(401, {"error": "token required"})
            b = self._body()
            try:
                if parts[1:] in (["audit"], ["xrefs"]):
                    text = b.get("text") or ""
                    if b.get("file_b64"):
                        text = load_text(base64.b64decode(b["file_b64"]), b.get("filename", ""))
                    if not text.strip():
                        return self._send(400, {"error": "empty manuscript"})
                    if parts[1] == "xrefs":
                        jid = start_job(cfg, db, lambda ctx: engine.crossref_check(ctx, text, b.get("title") or b.get("filename") or "manuscript"))
                        return self._send(202, {"job": jid})
                    jid = start_job(cfg, db, lambda ctx: engine.audit(ctx, text, b.get("title") or b.get("filename") or "manuscript",
                                                                     b.get("bib") or None, int(b.get("contexts", 100)), bool(b.get("deep"))))
                    return self._send(202, {"job": jid})
                if parts[1:] == ["paper"]:
                    jid = start_job(cfg, db, lambda ctx: engine.paper_report(ctx, b["doi"], int(b.get("contexts", 200)), bool(b.get("deep"))))
                    return self._send(202, {"job": jid})
                if parts[1:] == ["counter"]:
                    jid = start_job(cfg, db, lambda ctx: engine.counter_evidence(ctx, b["claim"], int(b.get("n", 15))))
                    return self._send(202, {"job": jid})
                if parts[1] == "runs" and len(parts) == 4 and parts[3] == "recheck":
                    jid = start_job(cfg, db, lambda ctx: engine.recheck(ctx, parts[2]))
                    return self._send(202, {"job": jid})
                if parts[1] == "runs" and len(parts) == 4 and parts[3] == "judgements":
                    run = db.load_run(parts[2])
                    rep = export.apply_judgements(engine.Ctx(cfg, db=db), run, b.get("reply") or b.get("_raw", ""),
                                                  b.get("rater", "unnamed"), b.get("rater_kind", "model"), b.get("role", "primary"))
                    return self._send(200, rep)
                if parts[1] == "runs" and len(parts) == 6 and parts[3] == "findings" and parts[5] == "status":
                    run = db.load_run(parts[2])
                    for f in run["findings"]:
                        if f["id"] == parts[4]:
                            f["status"] = b["status"]
                    db.save_run(run)
                    return self._send(200, {"ok": True})
            except (KeyError, ValueError) as e:
                return self._send(400, {"error": str(e)})
            return self._send(404, {"error": "not found"})
    return H


def serve(cfg, host="127.0.0.1", port=8765):
    db = DB(cfg.db_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(cfg, db))
    if host not in ("127.0.0.1", "localhost") and not cfg.token:
        print("warning: listening on a public interface without REFWARDEN_TOKEN")
    print(f"refwarden {__version__} on http://{host}:{port}  (db: {cfg.db_path})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
