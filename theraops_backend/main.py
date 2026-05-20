from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv() 

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from theraops_backend.core.config import validate_incident_memory_path
from theraops_backend.routers.health import router as health_router
from theraops_backend.routers.incidents import router as incidents_router
from theraops_backend.routers.slack import router as slack_router
from theraops_backend.runtime import build_services, close_services
from theraops_backend.core.services import get_services
from theraops_backend.backend.fake_alertdata import generate_fake_network_events
from theraops_backend.backend.categorizer import categorize_event, ISSUE_CATEGORIES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method != "POST":
            return await call_next(request)

        start = time.monotonic()

        # Read body and re-provide it so the app can still read it
        service = None
        try:
            body = await request.body()
            # This is the trick to let the next handler still read the body
            async def receive():
                return {"type": "http.request", "body": body}
            request._receive = receive

            if body:
                body_json = json.loads(body)
                service = body_json.get("service")
        except Exception:
            pass

        response: Response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000)

        log_entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": request.url.path,
            "duration_ms": duration_ms,
            "status": response.status_code,
        }
        if service:
            log_entry["service"] = service
        if response.status_code >= 400:
            log_entry["error_message"] = f"HTTP {response.status_code}"

        logger.info(json.dumps(log_entry))
        return response


def create_app() -> FastAPI:
    services = build_services()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = services
        watcher_task: asyncio.Task[None] | None = None

        validate_incident_memory_path(services.settings)

        if services.settings.run_watcher_in_api:
            watcher_task = asyncio.create_task(services.watcher.run_forever())
            app.state.watcher_task = watcher_task
        else:
            logger.info("Watcher is disabled inside the API process.")

        if not services.settings.internal_api_token:
            logger.error("🚨 CRITICAL SECURITY ISSUE: THERAOPS_INTERNAL_API_TOKEN is not set. Internal API routes are currently open to the world. Set it immediately in .env!")

        try:
            yield
        finally:
            services.watcher.stop()
            if watcher_task is not None:
                watcher_task.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher_task

            await close_services(services)

    app = FastAPI(title="TheraOps Backend", lifespan=lifespan)
    app.add_middleware(RequestLoggerMiddleware)
    app.include_router(health_router)
    app.include_router(slack_router)
    app.include_router(incidents_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": "TheraOps Backend", "status": "ready"}

    @app.get("/api/issues/by-network")
    async def issues_by_network(request: Request, source: str = "fake", hours: int = 24) -> dict:
        services = get_services(request)

        if source == "fake":
            events = generate_fake_network_events()
        elif source == "graylog":
            events = await services.graylog.get_network_issues(hours)
        else:
            return {}

        networks: dict[str, dict] = {}
        # prepare category keys
        category_keys = list(ISSUE_CATEGORIES.keys())

        for ev in events:
            net = ev.get("network_code") or "unknown"
            entry = networks.get(net)
            if entry is None:
                entry = {
                    "network_code": net,
                    "network_id": ev.get("network_id"),
                    "total_issues": 0,
                    "issues_by_category": {k: 0 for k in category_keys},
                    "affected_devices": set(),
                    "last_seen": None,
                }
                networks[net] = entry

            entry["total_issues"] += 1
            cat = categorize_event(ev.get("event_code", ""))
            if cat in entry["issues_by_category"]:
                entry["issues_by_category"][cat] += 1

            device_name = ev.get("device_name") or ev.get("device_id")
            if device_name:
                entry["affected_devices"].add(str(device_name))

            ts = ev.get("timestamp")
            if ts:
                # keep most recent ISO string
                if not entry["last_seen"] or ts > entry["last_seen"]:
                    entry["last_seen"] = ts

        # convert sets and sort networks by total_issues desc
        sorted_nets = dict()
        for net_code, entry in sorted(networks.items(), key=lambda kv: kv[1]["total_issues"], reverse=True):
            affected_devices_list = sorted(list(entry["affected_devices"]))
            sorted_nets[net_code] = {
                "network_code": entry["network_code"],
                "network_id": entry["network_id"],
                "total_issues": entry["total_issues"],
                "issues_by_category": entry["issues_by_category"],
                "affected_devices": affected_devices_list,
                # also expose device ids explicitly for consumers that need ids
                "affected_device_ids": affected_devices_list,
                "last_seen": entry["last_seen"],
            }

        return sorted_nets

    return app


app = create_app()

