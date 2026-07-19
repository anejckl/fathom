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


# Quick NL parser — handles time/level/keyword extraction without Ollama
import re as _re
_NL_STOPWORDS = frozenset({
    'what', 'which', 'when', 'how', 'did', 'does', 'show', 'me', 'find',
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'have', 'has', 'and',
    'or', 'in', 'on', 'at', 'for', 'to', 'do', 'get', 'any',
})
_NL_TIME_WORDS = frozenset({
    'today', 'tonight', 'yesterday', 'hour', 'hours', 'night',
    'week', 'day', 'days', 'last', 'ago', 'recent', 'now',
})
import datetime as _dt
_NL_TIME_PHRASES = [
    (_re.compile(r'(\d+)\s+minutes?\s+ago'),  'N_min_ago'),
    (_re.compile(r'(\d+)\s+hours?\s+ago'),    'N_hr_ago'),
    (_re.compile(r'last\s+(\d+)\s+minutes?'), 'last_N_min'),
    (_re.compile(r'last\s+minute'),              'last_1_min'),
    (_re.compile(r'last\s+(\d+)\s+hours?'),   'last_N_hr'),
    (_re.compile(r'last\s+hour'),                'last_1_hr'),
    (_re.compile(r'last\s+night'),               'last_night'),
    (_re.compile(r'last\s+week'),                'last_week'),
    (_re.compile(r'last\s+(\d+)\s+days?'),    'last_N_days'),
    (_re.compile(r'\btonight\b'),              'tonight'),
    (_re.compile(r'\btoday\b'),                'today'),
    (_re.compile(r'\byesterday\b'),            'yesterday'),
    (_re.compile(r'\brecent\b'),               'recent'),
]

def _nl_time_window(key, match=None):
    """Return (since_ts, until_ts). until_ts=None means open-ended (up to now)."""
    now_ts = int(time.time())
    now_dt = _dt.datetime.now()
    midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == 'last_N_min':
        return now_ts - int(match.group(1)) * 60, None
    if key == 'last_1_min':
        return now_ts - 60, None
    if key == 'last_N_hr':
        return now_ts - int(match.group(1)) * 3600, None
    if key == 'last_1_hr':
        return now_ts - 3600, None
    if key == 'last_night':
        # yesterday 20:00 -> today 06:00
        since = midnight - _dt.timedelta(hours=4)
        until = midnight + _dt.timedelta(hours=6)
        return int(since.timestamp()), int(until.timestamp()) if now_dt.hour >= 6 else None
    if key == 'last_week':
        return now_ts - 7 * 86400, None
    if key == 'last_N_days':
        return now_ts - int(match.group(1)) * 86400, None
    if key == 'today':
        return int(midnight.timestamp()), None
    if key == 'yesterday':
        yd = midnight - _dt.timedelta(days=1)
        return int(yd.timestamp()), int(midnight.timestamp())
    if key == 'tonight':
        tonight_start = midnight + _dt.timedelta(hours=18)
        tonight_end   = midnight + _dt.timedelta(days=1)
        return int(tonight_start.timestamp()), int(tonight_end.timestamp())
    if key == 'N_min_ago':
        return now_ts - int(match.group(1)) * 60, None
    if key == 'N_hr_ago':
        return now_ts - int(match.group(1)) * 3600, None
    if key == 'recent':
        return now_ts - 1800, None  # last 30 minutes
    return now_ts - 3600, None
_NL_STEMS = ('ing', 'ted', 'red', 'ed', 'es', 's')
_NL_LEVEL_WORDS = frozenset({'error','errors','warning','warnings','warn','info','critical','crit','fatal'})
_NL_LEVEL_PATTERNS = [
    (_re.compile(r'\b(error|errors|critical|crit|fatal)\b'), 'error'),
    (_re.compile(r'\b(warning|warnings|warn)\b'), 'warning'),
    (_re.compile(r'\binfo\b'), 'info'),
]

def _quick_nl_parse(q: str, known_containers=None) -> dict:
    ql = q.lower()
    result = {}
    for pat, key in _NL_TIME_PHRASES:
        m = pat.search(ql)
        if m:
            since_ts, until_ts = _nl_time_window(key, m)
            result['since_ts'] = since_ts
            if until_ts is not None:
                result['until_ts'] = until_ts
            break
    for pat, lvl in _NL_LEVEL_PATTERNS:
        if pat.search(ql):
            result['level'] = lvl
            break
    container_words = set()
    if known_containers:
        short_map = {}
        for full in known_containers:
            short = full
            if short.startswith('docker-'):
                short = short[7:]
            short = _re.sub(r'-\d+$', '', short)
            short_map[short] = full
        for w in _re.findall(r'\b[a-z][a-z0-9-]{1,}\b', ql):
            if w in short_map:
                result['container'] = short_map[w]
                container_words.add(w)
                break
    words = _re.findall(r'\b[a-z][a-z0-9]{2,}\b', ql)
    for w in words:
        if w in _NL_STOPWORDS or w in _NL_TIME_WORDS:
            continue
        if w in _NL_LEVEL_WORDS:
            continue
        if w in container_words:
            continue
        for sfx in _NL_STEMS:
            if w.endswith(sfx) and len(w) - len(sfx) >= 3:
                w = w[:-len(sfx)]
                break
        result['search'] = w
        break
    return result

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
    since: int = None, until: int = None, limit: int = 300, offset: int = 0
):
    return db.get_logs(container=container, level=level, project=project,
                       since=since, until=until, limit=limit, offset=offset)

@app.get("/api/search")
async def search(q: str = "", limit: int = 200):
    if not q.strip():
        return db.get_logs(limit=limit)
    # Parse NL signals first — detects time/level/container before FTS5 fires
    known = db.get_distinct_containers()
    quick = _quick_nl_parse(q, known)
    has_nl = any(k in quick for k in ('since_ts', 'level', 'container'))
    q_since = quick.get('since_ts')
    q_until = quick.get('until_ts')
    q_level = quick.get("level")
    q_container = quick.get("container")
    q_kw = quick.get("search")
    if has_nl:
        # Structured path: FTS5 keyword + DB filters for time/level/container
        if q_kw:
            try:
                results = db.fts_search(q_kw, limit=limit, since=q_since, until=q_until, level=q_level, container=q_container)
            except Exception:
                results = []
            if results:
                return results
        return db.get_logs(container=q_container, level=q_level, since=q_since, until=q_until, limit=limit)
    # Pure keyword — no NL signals detected, use raw FTS5
    try:
        results = db.fts_search(q, limit=limit)
    except Exception:
        results = []
    if results:
        return results
    # Ollama fallback — only for queries that look NL but quick parse missed
    loop = asyncio.get_event_loop()
    filters = await loop.run_in_executor(None, nl_to_filter, q)
    o_since = int(time.time()) - int(filters["since_hours"]) * 3600 if "since_hours" in filters else None
    o_kw = filters.get("search", "")
    if o_kw:
        try:
            results = db.fts_search(o_kw, limit=limit, since=o_since)
        except Exception:
            results = []
        if results:
            return results
    return db.get_logs(
        container=filters.get("container"),
        level=filters.get("level"),
        since=o_since,
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
async def stream(request: Request):
    """Tide -- SSE live log stream."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.append(q)
    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield "data: " + json.dumps(entry) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            try:
                _subscribers.remove(q)
            except ValueError:
                pass
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
