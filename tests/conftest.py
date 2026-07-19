import asyncio, datetime, pytest
import database as db_module

SEED_TS = 1750000000  # ~2025-06-15 14:46 UTC — fixed anchor for all seeded data


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_file)
    db_module.init_db()
    yield db_file


@pytest.fixture
def seeded_db(tmp_db):
    now = SEED_TS
    rows = [
        # (ts, container, image, project, level, line, parsed_msg, stream)
        (now,           "web-1",    "myapp:1",  "myapp", "info",    "Server started on port 8080", "",           "stdout"),
        (now - 30,      "web-1",    "myapp:1",  "myapp", "error",   "connection Error: timeout",   "",           "stdout"),
        (now - 60,      "web-1",    "myapp:1",  "myapp", "error",   "Unhandled exception raised",  "",           "stdout"),
        (now - 1800,    "web-1",    "myapp:1",  "myapp", "warning", "warn: slow query detected",   "",           "stdout"),
        (now - 7200,    "web-1",    "myapp:1",  "myapp", "info",    "GET /api/users 200 OK",       "",           "stdout"),
        (now - 86400,   "web-1",    "myapp:1",  "myapp", "error",   "fatal: disk full",            "",           "stdout"),
        (now - 86400*2, "web-1",    "myapp:1",  "myapp", "info",    "running database migrations", "migrations", "stdout"),
        (now - 10,      "worker-1", "worker:1", "myapp", "error",   "panic: nil pointer",          "",           "stdout"),
        (now - 120,     "worker-1", "worker:1", "myapp", "warning", "WARNING: deprecated API",     "",           "stdout"),
        (now - 3600,    "worker-1", "worker:1", "myapp", "info",    "Job completed",               "",           "stdout"),
        (now - 86400,   "worker-1", "worker:1", "myapp", "error",   "CRITICAL: out of memory",     "",           "stdout"),
        (now - 5,       "redis-1",  "redis:7",  "infra", "info",    "Server started",              "",           "stdout"),
        (now - 300,     "redis-1",  "redis:7",  "infra", "warning", "warn: memory usage high",     "",           "stdout"),
        (now - 7200,    "redis-1",  "redis:7",  "infra", "error",   "connection Error: refused",   "",           "stdout"),
        (now - 86400,   "redis-1",  "redis:7",  "infra", "info",    "Replication sync done",       "",           "stdout"),
        (now - 86400*3, "redis-1",  "redis:7",  "infra", "info",    "server started successfully", "started",    "stdout"),
    ]
    for r in rows:
        db_module.insert_log(*r)
    return {"db_path": tmp_db, "containers": ["web-1", "worker-1", "redis-1"], "now": now}


@pytest.fixture
def client(seeded_db, monkeypatch):
    async def _noop_collector():
        await asyncio.sleep(9999)

    async def _noop_alerter(conn_fn):
        await asyncio.sleep(9999)

    import main as main_module
    import auth as auth_module
    monkeypatch.setattr(main_module, "start_collector", _noop_collector)
    monkeypatch.setattr(main_module, "run_alerter", _noop_alerter)
    monkeypatch.setattr(auth_module, "AUTH_DISABLED", True)

    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def frozen_now():
    from freezegun import freeze_time
    dt = datetime.datetime(2025, 6, 15, 14, 30, 0)
    with freeze_time(dt):
        yield dt
