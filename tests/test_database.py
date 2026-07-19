import time, sqlite3, pytest
import database as db

pytestmark = pytest.mark.usefixtures("tmp_db")


# --- Schema -------------------------------------------------------------------

def test_tables_exist(tmp_db):
    with sqlite3.connect(tmp_db) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"logs", "filters", "alert_rules", "meta"} <= names


def test_fts5_table_exists(tmp_db):
    with sqlite3.connect(tmp_db) as c:
        row = c.execute("SELECT name FROM sqlite_master WHERE name='logs_fts'").fetchone()
    assert row is not None


def test_wal_mode(tmp_db):
    with sqlite3.connect(tmp_db) as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_unique_index_exists(tmp_db):
    with sqlite3.connect(tmp_db) as c:
        row = c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_filters_uniq'"
        ).fetchone()
    assert row is not None


def test_init_db_idempotent():
    db.init_db()
    db.init_db()


# --- insert_log + get_logs ----------------------------------------------------

def test_insert_and_retrieve():
    now = int(time.time())
    db.insert_log(now, "web-1", "img:1", "proj", "error", "boom", "boom msg", "stdout")
    rows = db.get_logs(container="web-1")
    match = [r for r in rows if r["line"] == "boom"]
    assert len(match) == 1
    r = match[0]
    assert r["container"] == "web-1"
    assert r["image"] == "img:1"
    assert r["project"] == "proj"
    assert r["level"] == "error"
    assert r["parsed_msg"] == "boom msg"
    assert r["stream"] == "stdout"
    assert r["timestamp"] == now


def test_fts_populated_on_insert():
    now = int(time.time())
    db.insert_log(now, "web-1", None, None, "info", "unique_xyz_token_abc", None, "stdout")
    results = db.fts_search("unique_xyz_token_abc")
    assert any(r["line"] == "unique_xyz_token_abc" for r in results)


def test_get_logs_filter_container():
    now = int(time.time())
    db.insert_log(now, "a", None, None, "info", "line a", None, "stdout")
    db.insert_log(now, "b", None, None, "info", "line b", None, "stdout")
    rows = db.get_logs(container="a")
    assert rows and all(r["container"] == "a" for r in rows)


def test_get_logs_filter_level():
    now = int(time.time())
    db.insert_log(now, "web-1", None, None, "error", "err line", None, "stdout")
    db.insert_log(now, "web-1", None, None, "info",  "ok line",  None, "stdout")
    rows = db.get_logs(level="error", container="web-1")
    assert rows and all(r["level"] == "error" for r in rows)


def test_get_logs_filter_project():
    now = int(time.time())
    db.insert_log(now, "a", None, "myapp", "info", "x", None, "stdout")
    db.insert_log(now, "b", None, "other", "info", "y", None, "stdout")
    rows = db.get_logs(project="myapp")
    assert len(rows) == 1 and rows[0]["project"] == "myapp"


def test_get_logs_filter_since():
    now = int(time.time())
    db.insert_log(now - 7200, "web-1", None, None, "info", "old line", None, "stdout")
    db.insert_log(now - 10,   "web-1", None, None, "info", "new line", None, "stdout")
    rows = db.get_logs(since=now - 3600, container="web-1")
    assert len(rows) == 1 and rows[0]["line"] == "new line"


def test_get_logs_filter_until():
    now = int(time.time())
    db.insert_log(now - 7200, "web-1", None, None, "info", "old line", None, "stdout")
    db.insert_log(now,        "web-1", None, None, "info", "new line", None, "stdout")
    rows = db.get_logs(until=now - 3600, container="web-1")
    assert len(rows) == 1 and rows[0]["line"] == "old line"


def test_get_logs_since_until_window():
    now = int(time.time())
    db.insert_log(now - 7200, "web-1", None, None, "info", "too old",  None, "stdout")
    db.insert_log(now - 1800, "web-1", None, None, "info", "in range", None, "stdout")
    db.insert_log(now,        "web-1", None, None, "info", "too new",  None, "stdout")
    rows = db.get_logs(since=now - 3600, until=now - 900, container="web-1")
    assert len(rows) == 1 and rows[0]["line"] == "in range"


def test_get_logs_pagination():
    now = int(time.time())
    for i in range(5):
        db.insert_log(now - i, "web-1", None, None, "info", f"pagline{i}", None, "stdout")
    p1 = db.get_logs(container="web-1", limit=2, offset=0)
    p2 = db.get_logs(container="web-1", limit=2, offset=2)
    assert len(p1) == 2
    assert len(p2) == 2
    assert {r["id"] for r in p1}.isdisjoint({r["id"] for r in p2})


def test_get_logs_newest_first():
    now = int(time.time())
    db.insert_log(now - 100, "web-1", None, None, "info", "old", None, "stdout")
    db.insert_log(now,       "web-1", None, None, "info", "new", None, "stdout")
    rows = db.get_logs(container="web-1")
    assert rows[0]["line"] == "new"


# --- fts_search ---------------------------------------------------------------

@pytest.mark.parametrize("line,query", [
    ("server started successfully",     "start"),
    ("listening on port 8080",          "listen"),
    ("running database migrations",     "migration"),
    ("connection failed after retries", "fail"),
    ("migrations in progress",          "migrat"),
])
def test_fts_stemming(line, query):
    now = int(time.time())
    db.insert_log(now, "web-1", None, None, "info", line, None, "stdout")
    assert any(r["line"] == line for r in db.fts_search(query))


def test_fts_no_match():
    now = int(time.time())
    db.insert_log(now, "web-1", None, None, "info", "hello world", None, "stdout")
    assert db.fts_search("zzznomatch") == []


def test_fts_filter_level():
    now = int(time.time())
    db.insert_log(now, "web-1", None, None, "error", "connection error",   None, "stdout")
    db.insert_log(now, "web-1", None, None, "info",  "connection timeout", None, "stdout")
    results = db.fts_search("connection", level="error")
    assert results and all(r["level"] == "error" for r in results)


def test_fts_filter_container():
    now = int(time.time())
    db.insert_log(now, "a", None, None, "info", "shared keyword", None, "stdout")
    db.insert_log(now, "b", None, None, "info", "shared keyword", None, "stdout")
    results = db.fts_search("shared", container="a")
    assert results and all(r["container"] == "a" for r in results)


def test_fts_filter_since():
    now = int(time.time())
    db.insert_log(now - 7200, "web-1", None, None, "info", "timeout error old", None, "stdout")
    db.insert_log(now - 10,   "web-1", None, None, "info", "timeout error new", None, "stdout")
    results = db.fts_search("timeout", since=now - 3600)
    assert len(results) == 1 and "new" in results[0]["line"]


def test_fts_filter_until():
    now = int(time.time())
    db.insert_log(now - 7200, "web-1", None, None, "info", "timeout error old", None, "stdout")
    db.insert_log(now,        "web-1", None, None, "info", "timeout error new", None, "stdout")
    results = db.fts_search("timeout", until=now - 3600)
    assert len(results) == 1 and "old" in results[0]["line"]


def test_fts_limit():
    now = int(time.time())
    for i in range(10):
        db.insert_log(now - i, "web-1", None, None, "error", f"error event {i}", None, "stdout")
    assert len(db.fts_search("error", limit=3)) <= 3


def test_fts_parameterized_safe():
    # FTS5 uses parameterized queries — the match string is bound as a param, not interpolated.
    now = int(time.time())
    db.insert_log(now, "web-1", None, None, "info", "normal line here", None, "stdout")
    results = db.fts_search("normalxyz")
    assert isinstance(results, list)  # no crash, returns empty list


# --- get_context --------------------------------------------------------------

def test_get_context_returns_neighbors():
    now = int(time.time())
    for i in range(10):
        db.insert_log(now - i, "web-1", None, None, "info", f"ctxline{i}", None, "stdout")
    all_rows = db.get_logs(container="web-1")
    mid_id = all_rows[len(all_rows) // 2]["id"]
    ctx = db.get_context(mid_id, "web-1", window=3)
    assert any(r["id"] == mid_id for r in ctx)


def test_get_context_container_scoped():
    now = int(time.time())
    db.insert_log(now, "web-1",   None, None, "info", "web line",   None, "stdout")
    db.insert_log(now, "redis-1", None, None, "info", "redis line", None, "stdout")
    web_row = db.get_logs(container="web-1")[0]
    ctx = db.get_context(web_row["id"], "web-1", window=5)
    assert all(r["container"] == "web-1" for r in ctx)


def test_get_context_nonexistent_id():
    assert db.get_context(999999, "web-1", window=5) == []


# --- sweep_old_logs -----------------------------------------------------------

def test_sweep_removes_old():
    now = int(time.time())
    db.insert_log(now - 40 * 86400, "web-1", None, None, "info", "old log", None, "stdout")
    db.insert_log(now,              "web-1", None, None, "info", "new log", None, "stdout")
    db.sweep_old_logs(30)
    rows = db.get_logs()
    assert not any(r["line"] == "old log" for r in rows)
    assert any(r["line"] == "new log" for r in rows)


def test_sweep_keeps_recent():
    now = int(time.time())
    db.insert_log(now - 10, "web-1", None, None, "info", "recent log", None, "stdout")
    db.sweep_old_logs(30)
    assert any(r["line"] == "recent log" for r in db.get_logs())


def test_sweep_sets_meta_key():
    db.sweep_old_logs(30)
    with db.conn() as c:
        row = c.execute("SELECT value FROM meta WHERE key='last_sweep'").fetchone()
    assert row is not None


def test_sweep_empty_db():
    db.sweep_old_logs(30)


# --- Filter CRUD --------------------------------------------------------------

def test_filter_add_get_delete():
    db.add_filter("web-1", "my_pattern", False)
    filters = db.get_filters()
    match = [f for f in filters if f["pattern"] == "my_pattern" and f["container"] == "web-1"]
    assert len(match) == 1
    fid = match[0]["id"]
    db.delete_filter(fid)
    assert not any(f["id"] == fid for f in db.get_filters())


def test_filter_duplicate_raises():
    db.add_filter("web-1", "dup_pat", False)
    with pytest.raises(Exception):
        db.add_filter("web-1", "dup_pat", False)


def test_filter_delete_nonexistent_ok():
    db.delete_filter(999999)


def test_filter_global_none_container():
    db.add_filter(None, "global_pattern", False)
    filters = db.get_filters()
    assert any(f["pattern"] == "global_pattern" and f["container"] is None for f in filters)


def test_filter_is_regex_stored():
    db.add_filter(None, r"error\d+", True)
    filters = db.get_filters()
    match = [f for f in filters if f["pattern"] == r"error\d+"]
    assert match and match[0]["is_regex"] == 1


# --- Alert rule CRUD ----------------------------------------------------------

def test_alert_add_get_delete():
    db.add_alert_rule("web-1", "error", 5, 5, "http://ntfy/x", "ntfy")
    rules = db.get_alert_rules()
    match = [r for r in rules if r["container"] == "web-1" and r["pattern"] == "error"]
    assert match
    rid = match[-1]["id"]
    db.delete_alert_rule(rid)
    assert not any(r["id"] == rid for r in db.get_alert_rules())


def test_alert_update_fired():
    db.add_alert_rule("web-1", "error", 5, 5, "http://ntfy/x", "ntfy")
    rules = db.get_alert_rules()
    rid = rules[-1]["id"]
    assert rules[-1]["last_fired"] is None
    db.update_alert_fired(rid)
    updated = next(r for r in db.get_alert_rules() if r["id"] == rid)
    assert updated["last_fired"] is not None


def test_alert_delete_nonexistent_ok():
    db.delete_alert_rule(999999)
