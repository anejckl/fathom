import asyncio, json, logging, os, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database as db
from collector import start_collector
from llm import nl_to_filter
from alerter import run_alerter
from database import conn

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("fathom")

RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "30"))

# SSE broadcast queue
_subscribers: list[asyncio.Queue] = []

_orig_insert = db.insert_log
def _insert_and_broadcast(*args, **kwargs):
    _orig_insert(*args, **kwargs)
    entry = dict(zip(
        ["timestamp","container","image","project","level","line","parsed_msg","stream"],
        args
    ))
    for q in list(_subscribers):
        try: q.put_nowait(entry)
        except asyncio.QueueFull: pass

db.insert_log = _insert_and_broadcast

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    collector_task = asyncio.create_task(start_collector())
    alerter_task   = asyncio.create_task(run_alerter(conn))
    sweep_task     = asyncio.create_task(_sweep_loop())
    yield
    collector_task.cancel()
    alerter_task.cancel()
    sweep_task.cancel()

async def _sweep_loop():
    while True:
        await asyncio.sleep(3600)
        db.sweep_old_logs(RETENTION_DAYS)
        log.info("Sweep: removed logs older than %d days", RETENTION_DAYS)

app = FastAPI(title="Fathom", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health")
async def health():
    return {"status": "ok", "ts": int(time.time())}

@app.get("/api/logs")
async def logs(
    container: str = None, level: str = None, project: str = None,
    since: int = None, limit: int = 300, offset: int = 0
):
    return db.get_logs(container=container, level=level, project=project,
                       since=since, limit=limit, offset=offset)

@app.get("/api/search")
async def search(q: str = "", limit: int = 200):
    if not q.strip():
        return db.get_logs(limit=limit)
    # try FTS5 first
    results = db.fts_search(q, limit=limit)
    if results:
        return results
    # Navigator fallback
    filters = nl_to_filter(q)
    since = None
    if "since_hours" in filters:
        since = int(time.time()) - int(filters["since_hours"]) * 3600
    kw = filters.get("search", "")
    if kw:
        results = db.fts_search(kw, limit=limit)
        if results:
            return results
    return db.get_logs(
        container=filters.get("container"),
        level=filters.get("level"),
        since=since,
        limit=limit
    )

@app.get("/api/context/{log_id}")
async def context(log_id: int, container: str = Query(...), window: int = 20):
    return db.get_context(log_id, container, window)

@app.get("/api/counts")
async def counts():
    return db.get_counts()

@app.get("/api/projects")
async def projects():
    return db.get_projects()

@app.get("/api/stream")
async def stream():
    """Tide — SSE live log stream."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.append(q)
    async def gen():
        try:
            while True:
                entry = await q.get()
                yield f"data: {json.dumps(entry)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _subscribers.remove(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# --- Mute (filters) ---
@app.get("/api/filters")
async def get_filters():
    return db.get_filters()

@app.post("/api/filters")
async def add_filter(req: Request):
    body = await req.json()
    db.add_filter(body.get("container"), body["pattern"], body.get("is_regex", False))
    return {"ok": True}

@app.delete("/api/filters/{filter_id}")
async def delete_filter(filter_id: int):
    db.delete_filter(filter_id)
    return {"ok": True}

# --- Flares (alert rules) ---
@app.get("/api/alerts")
async def get_alerts():
    return db.get_alert_rules()

@app.post("/api/alerts")
async def add_alert(req: Request):
    body = await req.json()
    db.add_alert_rule(
        body["container"], body["pattern"],
        body.get("threshold", 5), body.get("window_minutes", 5),
        body["webhook_url"], body["webhook_type"]
    )
    return {"ok": True}

@app.delete("/api/alerts/{rule_id}")
async def delete_alert(rule_id: int):
    db.delete_alert_rule(rule_id)
    return {"ok": True}

@app.get("/api/containers/status")
async def containers_status():
    import docker as docker_sdk
    try:
        client = docker_sdk.DockerClient(base_url="unix://var/run/docker.sock")
        return {c.name: c.status for c in client.containers.list(all=True)}
    except Exception:
        return {}
