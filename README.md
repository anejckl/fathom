# Fathom

**Fathom what's happening in your stack.**

[![Docker Image](https://ghcr.io/anejckl/fathom)](https://github.com/anejckl/fathom/pkgs/container/fathom)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Fathom is a persistent Docker log aggregator for homelabs. It automatically discovers every container, tails stdout/stderr in real time, stores logs in SQLite, and lets you search days later — in plain English if you have Ollama running.

Fills the gap between Dozzle (real-time only, no history) and Loki (heavy, multi-service stack).

---

## Features

- **Zero config** — auto-discovers and tails every running container, no labels or config files needed
- **Persistent** — logs survive restarts, searchable hours or days later via FTS5
- **Live stream** — new lines appear instantly in the browser via SSE (Tide)
- **JSON-aware** — extracts `level` and `msg` from structured log lines (Go zap, Python logging, Node winston)
- **NL search** — ask in plain English: *"errors last night"*, *"what restarted today"* (requires Ollama)
- **Noise filters** — suppress health check spam per-container or globally, managed from the UI
- **Webhook alerts** — get notified on ntfy / Discord / Slack when errors spike past a threshold
- **Compose grouping** — containers are grouped by their Compose project in the sidebar
- **Lightweight** — single container, ~50 MB RAM, SQLite database

---

## Quick start

```yaml
services:
  fathom:
    image: ghcr.io/anejckl/fathom:latest
    container_name: fathom
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - RETENTION_DAYS=30
      - TZ=Europe/London
      # optional — remove if you don't have Ollama:
      - OLLAMA_URL=http://ollama:11434
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - fathom-data:/data

volumes:
  fathom-data:
```

Then open `http://localhost:8000`.

---

## Without Ollama

Remove `OLLAMA_URL` — everything works, NL search falls back to SQLite FTS5 keyword search.

---

## NL search examples

> errors last night  
> what restarted today  
> sonarr warnings  
> database connection  
> last 6 hours

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | *(unset)* | Ollama base URL for NL search; disabled if unset |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model to use for NL search |
| `RETENTION_DAYS` | `30` | Delete logs older than N days |
| `RATE_LIMIT` | `20` | Max lines stored per container per minute |
| `LOG_LEVEL` | `info` | Python log level for Fathom itself |
| `TZ` | `UTC` | Timezone for display |

---

## vs Dozzle / Loki

| | Fathom | Dozzle | Loki |
|---|---|---|---|
| Persistent history | Yes | No | Yes |
| Zero config | Yes | Yes | No |
| NL search | Yes (Ollama) | No | No |
| Single container | Yes | Yes | No (4+ services) |
| RAM | ~50 MB | ~20 MB | 500 MB+ |

---

## License

MIT
