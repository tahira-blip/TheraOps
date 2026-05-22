# T-hera: Slack Bot for Viana Device, Sensor, and Network Diagnostics

A developer-friendly repository for a Slack-based diagnostics assistant designed to support Viana device, sensor, and network operations.

This repo contains an initial prototype and architectural foundation rather than a finished product. The concept is under active development, and the code is structured so other developers can continue building on the original idea.

The project is composed of two main components:

- `thera-bot/` — Slack bot implementation in TypeScript using `@slack/bolt`
- `theraops_backend/` — FastAPI backend in Python for Graylog diagnostics, incident memory, and AI-assisted analysis

---

## What this project does

- Listens to Slack events: bot mentions, direct messages, shortcuts
- Routes Slack requests to a backend service for analysis of device/service issues
- Uses Graylog and internal incident memory to surface device, sensor, and network diagnostics
- Provides Slack-ready diagnostic replies and summary blocks
- Supports backend workflows for log lookups, offline investigations, device diagnostics, and network issue summaries

---

## Project status

- This project is a partially developed prototype and not yet production-ready.
- The original concept is captured in the repo structure, Slack bot workflow, and backend API design.
- Future contributors should focus on completing the Slack interaction patterns, backend diagnostics flow, and environment configuration.

---

## Project structure

- `thera-bot/`
  - `src/index.ts` — Slack bot app bootstrapping and handler registration
  - `src/handlers/` — command, mention, DM, shortcut, log action handlers
  - `src/listeners/threads.ts` — thread message processing
  - `src/lib/` — Slack message formatting and backend request helpers
  - `.env.example` — Slack environment variables template

- `theraops_backend/`
  - `main.py` — FastAPI app composition and startup lifecycle
  - `routers/` — backend API routes for Slack, health, incidents, and diagnostics
  - `core/` — configuration, auth, settings, and service registry
  - `monitoring/` — Graylog client, watcher, and alerting logic
  - `memory/` — incident memory for past investigation recall
  - `diagnostics/` — orchestration of device diagnostics and provider integrations

---

## Key features

- Slack-based diagnostics for Viana services and devices
- Backend support for Graylog log summaries and error investigation
- AI-assisted diagnosis through mentor integration
- Device offline investigation workflow
- Diagnostics endpoint for device health and sensor connectivity
- Network summary endpoints for issue aggregation
- Internal API protection via token-based middleware

---

## Prerequisites

- Node.js 20+ (or compatible LTS) for `thera-bot`
- Python 3.12+ for `theraops_backend`
- Slack app credentials with bot token, signing secret, and socket mode app token
- Graylog access for log and network diagnostics
- Optional: LLM/mentor credentials if AI features are enabled

---

## Setup

### 1. Install bot dependencies

```bash
cd thera-bot
npm install
```

### 2. Install backend dependencies

```bash
cd ../theraops_backend
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in both `thera-bot/` and `theraops_backend/` as needed.

#### `thera-bot/.env`

```text
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
SLACK_APP_TOKEN=
ADMIN_SLACK_USER_ID=
```

#### `theraops_backend/.env`

```text
THERAOPS_INTERNAL_API_TOKEN=
GRAYLOG_URL=
GRAYLOG_TOKEN=
SLACK_BOT_TOKEN=
```

> `THERAOPS_INTERNAL_API_TOKEN` is required to protect internal Slack API routes like `/slack/logs`, `/slack/chat`, and `/slack/diagnostics`.

---

## Run locally

### Start the backend

```bash
cd theraops_backend
uvicorn theraops_backend.main:app --reload --host 127.0.0.1 --port 8000
```

### Start the Slack bot

```bash
cd ../thera-bot
npm run dev
```

---

## Developer notes

- `thera-bot/src/index.ts` validates Slack tokens and starts the Bolt app in socket mode
- Bot handlers forward Slack requests to the backend when users ask for diagnostics or log analysis
- Backend middleware logs request metadata and enforces internal API authentication
- `theraops_backend/routers/slack.py` exposes core endpoints used by the bot
- `theraops_backend/main.py` also includes public network summary endpoints for diagnostics consumers

---

## Useful endpoints in the backend

- `POST /slack/logs` — fetch log summary and diagnostics for a service
- `POST /slack/offline` — perform offline device/service investigation
- `POST /slack/chat` — analyze thread/chat intent and fetch logs or answer questions
- `POST /slack/diagnostics` — run full device diagnostics for a device ID
- `GET /api/issues/by-network` — aggregate network issue counts
- `GET /api/issues/network-summary` — fuzzy-join events, logs, and heartbeats for network status

---

## Recommended next steps

- Track service aliases used by the bot in the backend registry
- Expand tests around Slack handler flows and backend diagnostics orchestration

---

## Notes

This repository is built for a Slack diagnostics workflow focused on Viana device/sensor/network monitoring. It is intentionally split into a lightweight Slack bot layer and a diagnostic backend layer so each can evolve independently.

The current implementation is intentionally incomplete: it is intended as a starting point for other developers, not a finished product. If you continue work here, concentrate on completing the Slack bot user experience, backend route integration, and Graylog/diagnostics workflows.
