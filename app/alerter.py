import asyncio, json, logging, os, re, time
import requests
from database import get_alert_rules, update_alert_fired

log = logging.getLogger("fathom.flares")

async def run_alerter(db_conn_fn):
    while True:
        await asyncio.sleep(60)
        try:
            _check_rules(db_conn_fn)
        except Exception as e:
            log.warning("Flares check error: %s", e)

def _check_rules(db_conn_fn):
    import sqlite3
    rules = get_alert_rules()
    if not rules:
        return
    now = int(time.time())
    for rule in rules:
        window_start = now - rule["window_minutes"] * 60
        last_fired   = rule["last_fired"] or 0
        # cooldown: don't re-fire within the window
        if now - last_fired < rule["window_minutes"] * 60:
            continue
        with db_conn_fn() as c:
            rows = c.execute(
                """SELECT line FROM logs
                   WHERE container=? AND timestamp>=? AND level='error'""",
                (rule["container"], window_start)
            ).fetchall()
        matches = [r["line"] for r in rows if _matches(r["line"], rule["pattern"])]
        if len(matches) >= rule["threshold"]:
            _fire(rule, matches[:3])
            update_alert_fired(rule["id"])

def _matches(line: str, pattern: str) -> bool:
    try:
        return bool(re.search(pattern, line, re.I))
    except re.error:
        return pattern.lower() in line.lower()

def _fire(rule: dict, sample_lines: list):
    wtype = rule["webhook_type"]
    url   = rule["webhook_url"]
    title = f"Fathom Flare: {rule['container']}"
    body  = f"{rule['threshold']} errors in {rule['window_minutes']}m matching `{rule['pattern']}`\n\n" + "\n".join(sample_lines)
    try:
        if wtype == "ntfy":
            requests.post(url, data=body.encode(), headers={"Title": title}, timeout=10)
        elif wtype == "discord":
            requests.post(url, json={"content": f"**{title}**\n{body}"}, timeout=10)
        elif wtype == "slack":
            requests.post(url, json={"text": f"*{title}*\n{body}"}, timeout=10)
        log.info("Flare fired for %s", rule["container"])
    except Exception as e:
        log.warning("Flare delivery failed: %s", e)
