import sqlite3, os, time, re
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "/data/fathom.db")

DEFAULT_FILTERS = [
    "GET /health", "GET /ping", "GET /healthz", "GET /ready",
    "healthcheck", "health_check", "kube-probe",
]

@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

def init_db():
    with conn() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    container TEXT NOT NULL,
    image     TEXT,
    project   TEXT,
    level     TEXT DEFAULT 'info',
    line      TEXT NOT NULL,
    parsed_msg TEXT,
    stream    TEXT DEFAULT 'stdout'
);
CREATE INDEX IF NOT EXISTS idx_logs_ts        ON logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_container ON logs(container);
CREATE INDEX IF NOT EXISTS idx_logs_level     ON logs(level);
CREATE VIRTUAL TABLE IF NOT EXISTS logs_fts
    USING fts5(line, container, parsed_msg, content=logs, content_rowid=id);

CREATE TABLE IF NOT EXISTS filters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    container  TEXT,
    pattern    TEXT NOT NULL,
    is_regex   INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS alert_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    container      TEXT NOT NULL,
    pattern        TEXT NOT NULL,
    threshold      INTEGER DEFAULT 5,
    window_minutes INTEGER DEFAULT 5,
    webhook_url    TEXT NOT NULL,
    webhook_type   TEXT NOT NULL,
    last_fired     INTEGER
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
""")
        for p in DEFAULT_FILTERS:
            c.execute("INSERT OR IGNORE INTO filters(container,pattern) VALUES(NULL,?)", (p,))

def insert_log(timestamp, container, image, project, level, line, parsed_msg, stream):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO logs(timestamp,container,image,project,level,line,parsed_msg,stream) VALUES(?,?,?,?,?,?,?,?)",
            (timestamp, container, image, project, level, line, parsed_msg, stream)
        )
        rowid = cur.lastrowid
        c.execute("INSERT INTO logs_fts(rowid,line,container,parsed_msg) VALUES(?,?,?,?)",
                  (rowid, line, container, parsed_msg or ""))

def get_logs(container=None, level=None, project=None, since=None, limit=300, offset=0):
    clauses, params = [], []
    if container: clauses.append("container=?"); params.append(container)
    if level:     clauses.append("level=?");     params.append(level)
    if project:   clauses.append("project=?");   params.append(project)
    if since:     clauses.append("timestamp>=?"); params.append(since)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with conn() as c:
        rows = c.execute(
            f"SELECT * FROM logs {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
    return [dict(r) for r in rows]

def fts_search(q, limit=200):
    with conn() as c:
        rows = c.execute(
            """SELECT l.* FROM logs_fts f
               JOIN logs l ON l.id = f.rowid
               WHERE logs_fts MATCH ?
               ORDER BY l.timestamp DESC LIMIT ?""",
            (q, limit)
        ).fetchall()
    return [dict(r) for r in rows]

def get_context(log_id, container, window=20):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM logs WHERE id BETWEEN ? AND ? AND container=? ORDER BY id",
            (log_id - window, log_id + window, container)
        ).fetchall()
    return [dict(r) for r in rows]

def get_counts():
    with conn() as c:
        rows = c.execute("SELECT container, COUNT(*) as n FROM logs GROUP BY container").fetchall()
        errors = c.execute("SELECT container, COUNT(*) as n FROM logs WHERE level='error' GROUP BY container").fetchall()
    counts = {r["container"]: r["n"] for r in rows}
    err_counts = {r["container"]: r["n"] for r in errors}
    return {"counts": counts, "errors": err_counts}

def get_projects():
    with conn() as c:
        rows = c.execute(
            "SELECT DISTINCT project, container FROM logs WHERE project IS NOT NULL ORDER BY project, container"
        ).fetchall()
    grouped = {}
    for r in rows:
        grouped.setdefault(r["project"], []).append(r["container"])
    with conn() as c:
        standalone = c.execute(
            "SELECT DISTINCT container FROM logs WHERE project IS NULL ORDER BY container"
        ).fetchall()
    if standalone:
        grouped["standalone"] = [r["container"] for r in standalone]
    return grouped

def get_filters():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM filters ORDER BY id").fetchall()]

def add_filter(container, pattern, is_regex=False):
    with conn() as c:
        c.execute("INSERT INTO filters(container,pattern,is_regex) VALUES(?,?,?)",
                  (container or None, pattern, int(is_regex)))

def delete_filter(filter_id):
    with conn() as c:
        c.execute("DELETE FROM filters WHERE id=?", (filter_id,))

def get_alert_rules():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alert_rules ORDER BY id").fetchall()]

def add_alert_rule(container, pattern, threshold, window_minutes, webhook_url, webhook_type):
    with conn() as c:
        c.execute(
            "INSERT INTO alert_rules(container,pattern,threshold,window_minutes,webhook_url,webhook_type) VALUES(?,?,?,?,?,?)",
            (container, pattern, threshold, window_minutes, webhook_url, webhook_type)
        )

def delete_alert_rule(rule_id):
    with conn() as c:
        c.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))

def update_alert_fired(rule_id):
    with conn() as c:
        c.execute("UPDATE alert_rules SET last_fired=? WHERE id=?", (int(time.time()), rule_id))

def sweep_old_logs(retention_days):
    cutoff = int(time.time()) - retention_days * 86400
    with conn() as c:
        c.execute("DELETE FROM logs WHERE timestamp<?", (cutoff,))
        c.execute("INSERT INTO meta(key,value) VALUES('last_sweep',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (str(int(time.time())),))
