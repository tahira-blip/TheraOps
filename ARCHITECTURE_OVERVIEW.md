**System Architecture Overview**

- **Purpose:** High-level overview of the T-hera system: Slack-facing bot (TypeScript) + FastAPI backend (Python) integrating with Graylog, incident memory and LLMs.

**Components**
- **Slack**: Receives user messages, mentions, threads and triggers bot flows.
- **T-hera Bot (TypeScript)**: Runs in `thera-bot/`.
  - Entry points: `src/index.ts`, handlers in `src/handlers/` (e.g., `command.ts`, `dm.ts`, `mention.ts`, `threads.ts`).
  - Responsibilities: parse messages, call backend endpoints, render Slack blocks, post messages.
- **FastAPI Backend (Python)**: `theraops_backend/`.
  - Entry: `main.py` exposing endpoints like `/slack/logs`.
  - Runtime pieces: `runtime.py` builds services, `memory/frieren_librarian.py` persists incident records, `monitoring/fern_watcher.py` polls data sources.
- **Data Sources**:
  - Graylog API (universal/relative) used for log queries.
  - Local JSON incident store: `data/incidents.json` (Frieren memory).
  - LLM(s) via `interface/flamme_mentor.py` (Gemini and custom endpoints).
- **Alerts & Outputs**:
  - Slack alerts and diagnosis blocks constructed by `thera-bot/src/lib/logMessage.ts`.

**Message flow (simplified)**
- Slack event -> T-hera handler -> (if ops query) call backend `/slack/logs` -> Backend queries Graylog & Frieren -> Backend/orchestrator calls LLM -> Reply composed -> Bot posts diagnosis blocks to Slack.
- Separate watcher (`fern_watcher`) polls for spikes and posts alerts to Slack which can surface diagnosis workflows.

**Runtime & Dev**
- Backend: run with uvicorn:

```bash
uvicorn theraops_backend.main:app --reload --host 127.0.0.1 --port 8000
```

- Bot: run in `thera-bot/` with `npm run dev` (TypeScript build/watch via `ts-node` or `tsc` + runner).

**Security & Config**
- `.env` must include `THERAOPS_INTERNAL_API_TOKEN` to lock backend internal routes.
- Other envs: Graylog URL/credentials, LLM endpoints, Slack tokens.

**Scaling & reliability notes**
- Graylog and LLM calls are network-bound; ensure timeouts/retries in `theraops_backend` HTTP client (`httpx`).
- Frieren JSON is OK for dev; migrate to durable DB (ChromaDB/Postgres) for production.
- Run backend behind a process manager (systemd/container) and the bot in a scalable environment (containers + replicas) for load.

**Files to review**
- [architecture.mmd](architecture.mmd)
- [thera-bot/src/lib/logMessage.ts](thera-bot/src/lib/logMessage.ts)
- [theraops_backend/memory/frieren_librarian.py](theraops_backend/memory/frieren_librarian.py)
- [theraops_backend/main.py](theraops_backend/main.py)

**Next suggested actions**
- Add `ARCHITECTURE_OVERVIEW.md` to repo (this file).
- Document environment variables in a `README.md` or `.env.example`.
- Replace `data/incidents.json` with a small local DB for concurrent writes.

-- generated on 2026-05-04
