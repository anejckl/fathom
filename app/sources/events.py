import asyncio, time, logging
from docker import DockerClient
from docker.errors import DockerException

log = logging.getLogger("fathom.events")

LIFECYCLE = {"start", "stop", "die", "oom", "kill", "restart", "pause", "unpause"}

async def collect_events(insert_fn, on_container_start=None, on_container_stop=None):
    loop = asyncio.get_event_loop()
    while True:
        try:
            await loop.run_in_executor(None, _stream_events, insert_fn, on_container_start, on_container_stop)
        except Exception as e:
            log.warning("Events stream error: %s — retrying in 5s", e)
            await asyncio.sleep(5)

def _stream_events(insert_fn, on_container_start, on_container_stop):
    from database import insert_log
    client = DockerClient(base_url="unix://var/run/docker.sock")
    for event in client.events(decode=True, filters={"type": "container"}):
        action = event.get("Action", "")
        if action not in LIFECYCLE:
            continue
        attrs   = event.get("Actor", {}).get("Attributes", {})
        name    = attrs.get("name", event.get("Actor", {}).get("ID", "unknown")[:12])
        image   = attrs.get("image", "")
        project = attrs.get("com.docker.compose.project")
        ts      = int(event.get("time", time.time()))
        level   = "error" if action in {"die", "oom", "kill"} else "info"
        title   = f"Container {action}: {name}"

        insert_fn(ts, name, image, project, level, title, title, "event")

        if on_container_start and action == "start":
            asyncio.run_coroutine_threadsafe(on_container_start(name), asyncio.get_event_loop())
        if on_container_stop and action in {"die", "stop"}:
            asyncio.run_coroutine_threadsafe(on_container_stop(name), asyncio.get_event_loop())
