import os, json, re, logging, requests

log = logging.getLogger("fathom.navigator")

OLLAMA_URL = os.getenv("OLLAMA_URL", "")
MODEL      = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

SYSTEM = """You are a log query parser. Extract filter parameters from natural language queries about container logs.
Return ONLY a JSON object with these optional keys:
- container: string (container name if mentioned)
- level: "error" | "warning" | "info" (if severity mentioned)
- since_hours: number (how many hours back, e.g. 24 for "last night", 1 for "last hour")
- search: string (keyword to search for if specific text mentioned)
Return {} if no filters can be extracted. No explanation, just JSON."""

def nl_to_filter(query: str) -> dict:
    if not OLLAMA_URL:
        return {}
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": MODEL, "stream": False,
                  "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": query}]},
            timeout=15
        )
        text = resp.json()["message"]["content"]
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        log.warning("Navigator error: %s", e)
    return {}
