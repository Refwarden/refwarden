"""SQLite store: HTTP cache, citation statements, runs, findings, ratings, watchlist.

One file, no server. Copy it, back it up, or point REFWARDEN_DB at a synced folder.
"""
import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY, status INTEGER, body BLOB, fetched_at REAL
);
CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cited_doi TEXT NOT NULL, citing_doi TEXT, citing_title TEXT, citing_year INTEGER,
    context TEXT NOT NULL, section TEXT, source TEXT, intents TEXT, is_influential INTEGER,
    stance TEXT, stance_method TEXT, stance_note TEXT, created_at REAL,
    UNIQUE (cited_doi, citing_doi, context)
);
CREATE INDEX IF NOT EXISTS idx_statements_cited ON statements(cited_doi);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, kind TEXT, title TEXT, sha256 TEXT, created_at TEXT,
    parent_run TEXT, result TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    run_id TEXT, finding_id TEXT, pass TEXT, location TEXT, defect TEXT, evidence TEXT,
    consequence TEXT, severity TEXT, confidence TEXT, min_repair TEXT, status TEXT,
    check_name TEXT, method TEXT,
    PRIMARY KEY (run_id, finding_id)
);
CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, item_id TEXT, rater TEXT, rater_kind TEXT,
    role TEXT, verdict TEXT, quote TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS watch (
    doi TEXT PRIMARY KEY, label TEXT, added TEXT, last_state TEXT, last_checked TEXT
);
"""


class DB:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def execute(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # --- cache ---
    def cache_get(self, key, max_age):
        rows = self.query("SELECT status, body, fetched_at FROM http_cache WHERE key=?", (key,))
        if not rows:
            return None
        r = rows[0]
        if max_age is not None and time.time() - r["fetched_at"] > max_age:
            return None
        return r["status"], r["body"]

    def cache_put(self, key, status, body):
        self.execute("INSERT OR REPLACE INTO http_cache(key, status, body, fetched_at) VALUES (?,?,?,?)",
                     (key, status, body, time.time()))

    # --- statements ---
    def add_statements(self, rows):
        with self.lock:
            for r in rows:
                self.conn.execute(
                    """INSERT INTO statements(cited_doi, citing_doi, citing_title, citing_year, context, section,
                       source, intents, is_influential, stance, stance_method, stance_note, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(cited_doi, citing_doi, context) DO UPDATE SET
                         stance=excluded.stance, stance_method=excluded.stance_method,
                         stance_note=excluded.stance_note, section=COALESCE(excluded.section, section)""",
                    (r["cited_doi"], r.get("citing_doi"), r.get("citing_title"), r.get("citing_year"), r["context"],
                     r.get("section"), r.get("source"), json.dumps(r.get("intents") or []),
                     int(bool(r.get("is_influential"))), r.get("stance"), r.get("stance_method"),
                     r.get("stance_note"), time.time()))
            self.conn.commit()

    # --- runs ---
    def save_run(self, result):
        self.execute("INSERT OR REPLACE INTO runs(run_id, kind, title, sha256, created_at, parent_run, result) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (result["run_id"], result["kind"], result.get("title"), result.get("sha256"),
                      result["created_at"], result.get("parent_run"), json.dumps(result)))
        with self.lock:
            self.conn.execute("DELETE FROM findings WHERE run_id=?", (result["run_id"],))
            for f in result.get("findings", []):
                self.conn.execute(
                    "INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (result["run_id"], f["id"], f["pass"], f["location"], f["defect"], f["evidence"],
                     f["consequence"], f["severity"], f["confidence"], f["min_repair"], f["status"],
                     f.get("check"), f.get("method")))
            self.conn.commit()

    def load_run(self, run_id):
        rows = self.query("SELECT result FROM runs WHERE run_id=?", (run_id,))
        if not rows:
            rows = self.query("SELECT result FROM runs WHERE run_id LIKE ? ORDER BY created_at DESC", (run_id + "%",))
        return json.loads(rows[0]["result"]) if rows else None

    def list_runs(self, limit=50):
        return self.query("SELECT run_id, kind, title, created_at, parent_run FROM runs "
                          "ORDER BY created_at DESC LIMIT ?", (limit,))
