import pytest

SEED_TS = 1750000000  # must match conftest.py


# --- /health ------------------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ts" in body


# --- /api/logs ----------------------------------------------------------------

def test_logs_200(client):
    r = client.get("/api/logs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) > 0


def test_logs_filter_container(client):
    r = client.get("/api/logs?container=web-1")
    assert r.status_code == 200
    rows = r.json()
    assert rows and all(row["container"] == "web-1" for row in rows)


def test_logs_filter_level(client):
    r = client.get("/api/logs?level=error")
    assert r.status_code == 200
    rows = r.json()
    assert rows and all(row["level"] == "error" for row in rows)


def test_logs_filter_project(client):
    r = client.get("/api/logs?project=infra")
    assert r.status_code == 200
    rows = r.json()
    assert rows and all(row["project"] == "infra" for row in rows)


def test_logs_filter_since(client):
    since = SEED_TS - 65
    r = client.get(f"/api/logs?container=web-1&since={since}")
    rows = r.json()
    assert all(row["timestamp"] >= since for row in rows)


def test_logs_filter_until(client):
    until = SEED_TS - 86400
    r = client.get(f"/api/logs?container=web-1&until={until}")
    rows = r.json()
    assert rows and all(row["timestamp"] <= until for row in rows)


def test_logs_since_until_window(client):
    since = SEED_TS - 65
    until = SEED_TS - 25
    r = client.get(f"/api/logs?container=web-1&since={since}&until={until}")
    rows = r.json()
    assert all(since <= row["timestamp"] <= until for row in rows)


def test_logs_limit(client):
    r = client.get("/api/logs?limit=3")
    assert r.status_code == 200
    assert len(r.json()) <= 3


def test_logs_offset_pagination(client):
    p1 = client.get("/api/logs?limit=4&offset=0").json()
    p2 = client.get("/api/logs?limit=4&offset=4").json()
    assert {r["id"] for r in p1}.isdisjoint({r["id"] for r in p2})


def test_logs_empty_result(client):
    future = SEED_TS + 99999
    r = client.get(f"/api/logs?since={future}")
    assert r.json() == []


# --- /api/search --------------------------------------------------------------

def test_search_level_filter(client):
    r = client.get("/api/search?q=errors")
    assert r.status_code == 200
    rows = r.json()
    assert rows and all(row["level"] == "error" for row in rows)


def test_search_empty_query(client):
    r = client.get("/api/search?q=")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_search_no_results(client):
    r = client.get("/api/search?q=zzznomatch_xyz_never")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_search_limit_param(client):
    r = client.get("/api/search?q=error&limit=2")
    assert r.status_code == 200
    assert len(r.json()) <= 2


# --- /api/context -------------------------------------------------------------

def test_context_valid_id(client):
    rows = client.get("/api/logs?container=web-1").json()
    log_id = rows[0]["id"]
    r = client.get(f"/api/context/{log_id}?container=web-1")
    assert r.status_code == 200
    ctx = r.json()
    assert isinstance(ctx, list)
    assert any(item["id"] == log_id for item in ctx)


def test_context_nonexistent_id(client):
    r = client.get("/api/context/999999?container=web-1")
    assert r.status_code == 200
    assert r.json() == []


# --- /api/counts --------------------------------------------------------------

def test_counts_structure(client):
    r = client.get("/api/counts")
    assert r.status_code == 200
    data = r.json()
    assert "counts" in data and "errors" in data


def test_counts_seeded_containers(client):
    counts = client.get("/api/counts").json()["counts"]
    for ctr in ("web-1", "worker-1", "redis-1"):
        assert ctr in counts


def test_counts_error_values(client):
    err_counts = client.get("/api/counts").json()["errors"]
    assert err_counts.get("web-1", 0) >= 2


# --- /api/projects ------------------------------------------------------------

def test_projects_structure(client):
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_projects_seeded(client):
    data = client.get("/api/projects").json()
    assert "myapp" in data and "infra" in data
    assert "web-1" in data["myapp"]
    assert "redis-1" in data["infra"]


# --- /api/stream --------------------------------------------------------------

@pytest.mark.skip(reason="SSE stream blocks on asyncio.wait_for — tested manually")
def test_stream_content_type(client):
    with client.stream("GET", "/api/stream") as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")


# --- /api/filters CRUD --------------------------------------------------------

def test_filters_get(client):
    r = client.get("/api/filters")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_filters_crud(client):
    r = client.post("/api/filters", json={"pattern": "test-mute-pattern", "is_regex": False})
    assert r.status_code == 200
    assert r.json().get("ok") is True

    filters = client.get("/api/filters").json()
    match = [f for f in filters if f["pattern"] == "test-mute-pattern"]
    assert len(match) == 1
    fid = match[0]["id"]

    r = client.delete(f"/api/filters/{fid}")
    assert r.status_code == 200
    assert not any(f["id"] == fid for f in client.get("/api/filters").json())


def test_filters_with_container(client):
    r = client.post("/api/filters", json={"pattern": "kube-probe-test", "container": "web-1"})
    assert r.status_code == 200
    filters = client.get("/api/filters").json()
    match = [f for f in filters if f["pattern"] == "kube-probe-test" and f["container"] == "web-1"]
    assert len(match) == 1


def test_filters_missing_pattern_errors(client):
    # App raises KeyError when 'pattern' missing (no Pydantic validation)
    with pytest.raises(Exception):
        client.post("/api/filters", json={"is_regex": False})


# --- /api/alerts CRUD ---------------------------------------------------------

def test_alerts_get(client):
    r = client.get("/api/alerts")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_alerts_crud(client):
    payload = {
        "container": "web-1",
        "pattern": "fatal",
        "threshold": 3,
        "window_minutes": 5,
        "webhook_url": "http://ntfy.sh/testfathom",
        "webhook_type": "ntfy",
    }
    r = client.post("/api/alerts", json=payload)
    assert r.status_code == 200
    assert r.json().get("ok") is True

    rules = client.get("/api/alerts").json()
    match = [r for r in rules if r["container"] == "web-1" and r["pattern"] == "fatal"]
    assert match
    rid = match[-1]["id"]

    r = client.delete(f"/api/alerts/{rid}")
    assert r.status_code == 200
    assert not any(r["id"] == rid for r in client.get("/api/alerts").json())


def test_alerts_missing_required_field(client):
    with pytest.raises(Exception):
        client.post("/api/alerts", json={"container": "web-1"})


# --- /api/containers/status ---------------------------------------------------

def test_containers_status_graceful(client):
    r = client.get("/api/containers/status")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
