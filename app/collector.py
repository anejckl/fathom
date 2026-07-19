import asyncio, logging
import docker
from database import insert_log
from sources.events import collect_events
from sources.logs import tail_container

log = logging.getLogger("fathom.collector")

_tail_tasks: dict[str, asyncio.Task] = {}
_stop_events: dict[str, asyncio.Event] = {}

def _get_container_meta(name: str):
    try:
        client = docker.DockerClient(base_url="unix://var/run/docker.sock")
        c = client.containers.get(name)
        labels = c.labels or {}
        return (
            c.image.tags[0] if c.image.tags else "",
            labels.get("com.docker.compose.project")
        )
    except Exception:
        return "", None

async def _start_tail(name: str):
    if name in _tail_tasks and not _tail_tasks[name].done():
        return
    image, project = _get_container_meta(name)
    stop_ev = asyncio.Event()
    _stop_events[name] = stop_ev
    task = asyncio.create_task(
        tail_container(name, image, project, insert_log, stop_ev),
        name=f"tail-{name}"
    )
    _tail_tasks[name] = task
    log.info("Started tail for %s", name)

async def _stop_tail(name: str):
    if name in _stop_events:
        _stop_events[name].set()
    if name in _tail_tasks:
        _tail_tasks[name].cancel()
    log.info("Stopped tail for %s", name)

async def start_collector():
    # tail all currently running containers
    try:
        client = docker.DockerClient(base_url="unix://var/run/docker.sock")
        for c in client.containers.list():
            await _start_tail(c.name)
    except Exception as e:
        log.error("Failed to enumerate containers: %s", e)

    # stream lifecycle events, hooking start/stop
    await collect_events(insert_log, on_container_start=_start_tail, on_container_stop=_stop_tail)
