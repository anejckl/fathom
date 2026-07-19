# Fathom

**Fathom what's happening in your stack.**

[![Tests](https://github.com/anejckl/fathom/actions/workflows/test.yml/badge.svg)](https://github.com/anejckl/fathom/actions/workflows/test.yml)
[![ghcr.io](https://img.shields.io/badge/ghcr.io-anejckl%2Ffathom-blue?logo=docker&logoColor=white)](https://github.com/anejckl/fathom/pkgs/container/fathom)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Fathom is a persistent Docker log aggregator built for homelabs. It fills the gap between Dozzle (real-time only, no history) and Loki (full stack, heavy). One container. Zero config. Search logs from last night in plain English.

![Fathom demo](docs/demo.gif)

---

## Features

- **Zero config** — auto-discovers every container via Docker socket, no labels or env vars required
- **Persistent** — logs survive container restarts and are searchable days later
- **Live stream** — new lines appear instantly via SSE, no polling
- **NL search** — built-in parser handles time/level/container queries; Ollama optional for free-form English
- **FTS5 + stemming** — `fail` finds `failed`, `failure`, `failing`; prefix matching included
- **Noise filters** — suppress health check spam at ingest time, before it hits the DB
- **Webhook alerts** — get notified on ntfy, Discord, or Slack when errors spike
- **Compose grouping** — sidebar groups containers by Docker Compose project
- **Log context** — click any line to see the 20 lines around it
- **Retention** — configurable auto-cleanup of old logs (default: 30 days)
- **Lightweight** — single container, ~50MB RAM, SQLite storage

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
      - FATHOM_USER=admin
      - FATHOM_PASSWORD=changeme
      - FATHOM_SECRET=change-this-to-a-random-string
      - RETENTION_DAYS=30
      - TZ=Europe/London
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - fathom-data:/data

volumes:
  fathom-data:
```

Open `http://localhost:8000` and sign in. Leave `FATHOM_PASSWORD` unset to disable auth (local dev).

---

## NL search examples

Fathom's built-in parser handles common queries instantly — no Ollama required:

| Query | What it does |
|-------|-------------|
| `errors today` | All error-level logs since midnight |
| `sonarr warnings` | Warnings from docker-sonarr-1 only |
| `critical last night` | Errors from yesterday 20:00 – today 06:00 |
| `radarr last hour` | All radarr logs in the last 60 minutes |
| `last 5 minutes` | Everything in the last 5 minutes |
| `45 minutes ago` | Logs since 45 minutes ago |
| `connection refused` | FTS5 keyword search with stemming |

Container shortnames are resolved automatically — type `sonarr` and Fathom maps it to `docker-sonarr-1`.

Level aliases: `critical`, `crit`, `fatal` → error · `warn` → warning

With `OLLAMA_URL` set, more complex free-form queries fall through to your local LLM.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `FATHOM_USER` | `admin` | Login username |
| `FATHOM_PASSWORD` | — | Login password. Leave unset to disable auth |
| `FATHOM_SECRET` | auto | Secret key for signing session cookies. Auto-generated and persisted in the DB if not set — sessions survive restarts. Set explicitly to share sessions across multiple instances or to rotate cookies on demand. |
| `RETENTION_DAYS` | `30` | Delete logs older than N days |
| `RATE_LIMIT` | `20` | Max lines per container per minute stored |
| `OLLAMA_URL` | — | Ollama base URL, e.g. `http://ollama:11434` |
| `OLLAMA_MODEL` | `llama3.2:latest` | Model to use for NL search |
| `LOG_LEVEL` | `info` | App log level |
| `TZ` | `UTC` | Timezone for timestamps |

---

## Noise filters

Fathom ships with default filters that suppress health check noise before it hits the database:

```
GET /health · GET /ping · GET /healthz · GET /ready · healthcheck · health_check · kube-probe
```

Add your own from the **Mute** panel in the UI — per-container or global, substring or regex.

---

## Webhook alerts

Configure alerts from the **Flares** panel. Supported targets: ntfy, Discord, Slack.

Each rule: container + error pattern + threshold (N errors in M minutes) + webhook URL. The alerter checks every 60 seconds and fires when the threshold is crossed.

---

## vs Dozzle / Loki

| | Fathom | Dozzle | Loki |
|---|---|---|---|
| Persistent logs | ✓ | — | ✓ |
| Zero config | ✓ | ✓ | — |
| NL search | ✓ | — | — |
| Authentication | ✓ | ✓ | ✓ |
| Single container | ✓ | ✓ | — |
| RAM usage | ~50MB | ~20MB | ~500MB+ |
| Setup | copy-paste compose | copy-paste compose | 4+ services |

---

## License

MIT
