import asyncio, json, re, time, logging
from collections import defaultdict
from database import get_filters

log = logging.getLogger("fathom.deckhand")

ERROR_RE  = re.compile(r'\b(error|exception|critical|fatal|panic)\b', re.I)
WARN_RE   = re.compile(r'\b(warn|warning|deprecated)\b', re.I)

_rate_buckets: dict[str, list] = defaultdict(list)
RATE_LIMIT = 20  # lines per container per minute

def _detect_level(line: str) -> str:
    if ERROR_RE.search(line): return "error"
    if WARN_RE.search(line):  return "warning"
    return "info"

def _parse_json(line: str) -> tuple[str, str]:
    """Returns (level, parsed_msg). Falls back to (detected_level, '')."""
    try:
        obj = json.loads(line)
        if not isinstance(obj, dict):
            return _detect_level(line), ""
        msg   = obj.get("msg") or obj.get("message") or obj.get("MESSAGE") or ""
        level = (obj.get("level") or obj.get("severity") or obj.get("lvl") or "").lower()
        if level not in ("error", "warning", "warn", "info", "debug", "critical", "fatal"):
            level = _detect_level(msg or line)
        elif level in ("warn",):
            level = "warning"
        elif level in ("critical", "fatal"):
            level = "error"
        return level, str(msg)
    except (json.JSONDecodeError, ValueError):
        return _detect_level(line), ""

def _rate_ok(container: str) -> bool:
    now = time.time()
    bucket = _rate_buckets[container]
    _rate_buckets[container] = [t for t in bucket if now - t < 60]
    if len(_rate_buckets[container]) >= RATE_LIMIT:
        return False
    _rate_buckets[container].append(now)
    return True

def _is_muted(line: str, container: str, filters: list) -> bool:
    for f in filters:
        if f["container"] and f["container"] != container:
            continue
        pattern = f["pattern"]
        if f["is_regex"]:
            try:
                if re.search(pattern, line, re.I):
                    return True
            except re.error:
                pass
        else:
            if pattern.lower() in line.lower():
                return True
    return False

async def tail_container(container_name: str, image: str, project: str, insert_fn, stop_event: asyncio.Event):
    import docker
    loop = asyncio.get_event_loop()
    log.info("Deckhand tailing: %s", container_name)
    try:
        client = docker.DockerClient(base_url="unix://var/run/docker.sock")
        container = client.containers.get(container_name)
        log_gen = container.logs(stream=True, follow=True, tail=100, timestamps=True)
        await loop.run_in_executor(None, _consume, log_gen, container_name, image, project, insert_fn, stop_event)
    except Exception as e:
        log.warning("Deckhand lost %s: %s", container_name, e)

def _consume(log_gen, container_name, image, project, insert_fn, stop_event):
    filters = get_filters()
    filters_ts = time.time()
    for raw in log_gen:
        if stop_event.is_set():
            break
        if time.time() - filters_ts > 30:
            filters = get_filters()
            filters_ts = time.time()
        try:
            raw_str = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            continue
        if not raw_str:
            continue
        ts = int(time.time())
        line = raw_str
        # Docker timestamps=True format: "2024-01-01T00:00:00.000000000Z <content>"
        if raw_str[0].isdigit():
            parts = raw_str.split(" ", 1)
            if len(parts) == 2 and parts[1].strip():
                line = parts[1].strip()
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
                    ts = int(dt.timestamp())
                except Exception:
                    pass
            else:
                # empty line after timestamp — skip
                continue
        if not line:
            continue
        if _is_muted(line, container_name, filters):
            continue
        if not _rate_ok(container_name):
            continue
        level, parsed_msg = _parse_json(line)
        insert_fn(ts, container_name, image, project, level, line, parsed_msg or None, "stdout")
