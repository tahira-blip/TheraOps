from __future__ import annotations

import os
import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import AliasChoices, BaseModel, Field

from theraops_backend.core.auth import verify_internal_request
from theraops_backend.core.service_registry import ServiceRegistry
from theraops_backend.core.services import get_services
from theraops_backend.investigations.offline import investigate_offline
from theraops_backend.monitoring.fern_watcher import ErrorSummary

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/slack",
    tags=["slack"],
    dependencies=[Depends(verify_internal_request)],
)


class LogsRequest(BaseModel):
    service: str = Field(min_length=1)
    window_seconds: int = Field(default=300, ge=60, le=2592000)


class LogsResponse(BaseModel):
    service: str
    device_service_id: str
    primary_stream: str
    error_count: int
    sample_messages: list[str]
    reply: str


class ThreadMessage(BaseModel):
    user: str | None = None
    role: str | None = None
    text: str = Field(default="")
    content: str | None = None
    ts: str | None = None


class ChatRequest(BaseModel):
    channel_id: str | None = None
    thread_ts: str | None = None
    user_id: str | None = None
    messages: list[ThreadMessage] = Field(default_factory=list)
    user_message: str | None = Field(default=None, min_length=1)
    service_alias: str | None = Field(default=None, min_length=1)
    thread_history: list[ThreadMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    action: str
    reply: str
    service: str | None = None
    device_service_id: str | None = None
    env: Literal["dev", "ppd"] | None = None
    window_seconds: int | None = None


class OfflineRequest(BaseModel):
    service_alias: str = Field(min_length=1)


class OfflineResponse(BaseModel):
    service: str
    device_service_id: str
    device_service_name: str
    graceful_disconnect_count: int
    critical_outage_count: int
    stale_count: int
    reply: str
    investigation: dict


class DiagnosticsRequest(BaseModel):
    device_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("device_id", "device_serial_id", "serial"),
    )
    window_seconds: int = Field(default=24 * 3600, ge=60, le=2592000)


class DiagnosticsResponse(BaseModel):
    device_id: str
    device_label: str | None = None
    device_status: str
    last_seen: str | None = None
    firmware_version: str | None = None
    connectivity_state: str | None = None
    telemetry_health: str | None = None
    sensor_health: str | None = None
    battery_power: str | None = None
    disconnect_reconnect_events: list[dict[str, str | None]]
    active_alerts: list[dict[str, str | None]]
    likely_issue_cause: str
    recommended_troubleshooting: list[str]
    ai_summary: str | None = None
    provider_errors: list[str] = []


def _service_registry() -> ServiceRegistry:
    watch_services_env = os.getenv("WATCH_SERVICES", "api,worker,vianapulse")
    valid_services = [s.strip() for s in watch_services_env.split(",") if s.strip()]
    return ServiceRegistry(allowed_service_ids=valid_services)


def _technical_context(
    *,
    actual_lucene_query: str,
    device_service_id: str,
    env: str | None,
    result_count: int | None = None,
    graylog_status: str = "ok",
) -> dict[str, str | int | None]:
    return {
        "search_type": "Universal Search performed across all indices",
        "query": actual_lucene_query,
        "service_id": device_service_id,
        "env": env,
        "result_count": result_count,
        "graylog_status": graylog_status,
    }


def _infer_env(text: str) -> Literal["dev", "ppd"] | None:
    lowered = text.lower()
    if "--dev" in lowered or re.search(r"\bdev(elopment)?\b", lowered):
        return "dev"
    if "--ppd" in lowered or re.search(r"\bppd\b", lowered):
        return "ppd"
    return None


def _infer_search_query(text: str) -> str | None:
    quoted = re.findall(r'"((?:\\.|[^"\\])*)"', text)
    if quoted:
        return quoted[-1].replace('\\"', '"').replace("\\\\", "\\").strip() or None
    return None


def _is_offline_query(text: str) -> bool:
    lowered = text.lower()
    return (
        "offline" in lowered
        or "stale" in lowered
        or "heartbeat" in lowered
        or "hard outage" in lowered
    )


def _message_text(message: ThreadMessage) -> str:
    return message.text or message.content or ""


def _infer_service_from_thread(messages: list[ThreadMessage], registry: ServiceRegistry):
    combined = "\n".join(_message_text(message) for message in messages)
    tokens = re.findall(r"[A-Za-z0-9._:-]+", combined)
    for token in reversed(tokens):
        resolved = registry.resolve(token)
        if resolved:
            return resolved
    return None


async def _fetch_graylog_summary(
    *,
    graylog,
    service: str,
    window_seconds: int,
    search_query: str | None,
    env: str | None,
    primary_stream: str,
) -> tuple[ErrorSummary, str]:
    try:
        summary = await graylog.fetch_error_summary(
            service=service,
            window_seconds=window_seconds,
            limit=5,
            search_query=search_query,
            env=env,
            primary_stream=primary_stream,
        )
        return summary, "ok"
    except Exception as exc:
        logger.exception("[SLACK CHAT] Graylog fetch failed for %s: %s", service, exc)
        return (
            ErrorSummary(
                service=service,
                error_count=0,
                sample_messages=[],
                status_code=503,
                sample_logs=[],
            ),
            "unreachable",
        )


@router.post("/logs", response_model=LogsResponse)
async def thera_logs(payload: LogsRequest, request: Request) -> LogsResponse:
    registry = _service_registry()
    resolved_service = registry.resolve(payload.service)

    if not resolved_service:
        error_log = {
            "path": "/slack/logs",
            "service": payload.service,
            "status": 400,
            "error": "Service not found or not monitored"
        }
        logger.error(error_log)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Service '{payload.service}' not found or not monitored. "
                f"Valid services or aliases: {registry.valid_names()}"
            ),
        )
    
    services = get_services(request)
    graylog = services.graylog
    memory = services.memory
    mentor = services.mentor

    actual_lucene_query = graylog.build_lucene_query(
        device_service_id=resolved_service.device_service_id,
        search_query=None,
        env=None,
    )
    summary, graylog_status = await _fetch_graylog_summary(
        graylog=graylog,
        service=resolved_service.device_service_id,
        window_seconds=payload.window_seconds,
        search_query=None,
        env=None,
        primary_stream=resolved_service.primary_stream,
    )
    technical_context = _technical_context(
        actual_lucene_query=actual_lucene_query,
        device_service_id=resolved_service.device_service_id,
        env=None,
        result_count=summary.error_count,
        graylog_status=graylog_status,
    )

    print(f'[SLACK LOGS] Fetched {summary.error_count} errors for {resolved_service.device_service_id}')
    similar_incidents = await memory.query_similar(
        service=resolved_service.device_service_id,
        sample_messages=summary.sample_messages,
    )
    print(f'[SLACK LOGS] Similar incidents found: {len(similar_incidents)}')
    for incident in similar_incidents:
        print(f'  - {incident.service}: {incident.root_cause[:50]}...')
    reply = await mentor.diagnose_logs(
        service=resolved_service.device_service_id,
        service_name=resolved_service.friendly_name,
        primary_stream=resolved_service.primary_stream,
        search_query=None,
        env=None,
        technical_context=technical_context,
        error_count=summary.error_count,
        sample_messages=summary.sample_messages,
        graylog_logs=summary.sample_logs or [],
        similar_incidents=similar_incidents,
    )

    print(f'[SLACK LOGS] Diagnosis complete, reply length: {len(reply)}')

    return LogsResponse(
        service=resolved_service.friendly_name,
        device_service_id=resolved_service.device_service_id,
        primary_stream=resolved_service.primary_stream,
        error_count=summary.error_count,
        sample_messages=summary.sample_messages,
        reply=reply,
    )


@router.post("/offline", response_model=OfflineResponse)
async def thera_offline(payload: OfflineRequest, request: Request) -> OfflineResponse:
    registry = _service_registry()
    services = get_services(request)

    try:
        investigation = await investigate_offline(
            payload.service_alias,
            registry=registry,
            graylog=services.graylog,
            mentor=services.mentor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return OfflineResponse(
        service=investigation.service_alias,
        device_service_id=investigation.device_service_id,
        device_service_name=investigation.device_service_name,
        graceful_disconnect_count=investigation.graceful_disconnect_count,
        critical_outage_count=investigation.critical_outage_count,
        stale_count=investigation.stale_count,
        reply=investigation.slack_report,
        investigation=investigation.to_dict(),
    )


@router.post("/chat", response_model=ChatResponse)
async def thera_chat(payload: ChatRequest, request: Request) -> ChatResponse:
    registry = _service_registry()
    receptionist_message = (payload.user_message or "").strip()
    if receptionist_message:
        history_messages = payload.thread_history + [
            ThreadMessage(role="user", text=receptionist_message)
        ]
        resolved_service = (
            registry.resolve(payload.service_alias)
            if payload.service_alias
            else _infer_service_from_thread(history_messages, registry)
        )
    else:
        history_messages = payload.messages
        resolved_service = _infer_service_from_thread(payload.messages, registry)

    if not resolved_service:
        return ChatResponse(
            action="ANSWER",
            reply=(
                "I could not identify a monitored service in this thread yet. "
                "Reply with the service name or ask about a monitored service alias."
            ),
        )

    combined_text = "\n".join(_message_text(message) for message in history_messages)
    env = _infer_env(combined_text)
    search_query = _infer_search_query(combined_text)

    services = get_services(request)
    graylog = services.graylog
    memory = services.memory
    mentor = services.mentor

    if _is_offline_query(combined_text):
        investigation = await investigate_offline(
            resolved_service.requested_name,
            registry=registry,
            graylog=graylog,
            mentor=mentor,
        )
        return ChatResponse(
            action="OFFLINE_INVESTIGATION",
            service=resolved_service.friendly_name,
            device_service_id=resolved_service.device_service_id,
            reply=investigation.slack_report,
        )

    if receptionist_message:
        intent = {"action": "FETCH_LOGS", "window_seconds": 300}
    else:
        intent = await mentor.analyze_chat_intent(
            [{"user": message.user or "", "text": _message_text(message), "ts": message.ts or ""} for message in payload.messages]
        )
    action = str(intent.get("action", "ANSWER"))

    if action != "FETCH_LOGS":
        return ChatResponse(
            action="ANSWER",
            service=resolved_service.friendly_name,
            device_service_id=resolved_service.device_service_id,
            env=env,
            reply=(
                "I am tracking this thread. If you want me to rerun logs, say "
                "`query an earlier interval` or `look further back`."
            ),
        )

    window_seconds = int(intent.get("window_seconds") or 3600)
    actual_lucene_query = graylog.build_lucene_query(
        device_service_id=resolved_service.device_service_id,
        search_query=search_query,
        env=env,
    )
    summary, graylog_status = await _fetch_graylog_summary(
        graylog=graylog,
        service=resolved_service.device_service_id,
        window_seconds=window_seconds,
        search_query=search_query,
        env=env,
        primary_stream=resolved_service.primary_stream,
    )
    technical_context = _technical_context(
        actual_lucene_query=actual_lucene_query,
        device_service_id=resolved_service.device_service_id,
        env=env,
        result_count=summary.error_count,
        graylog_status=graylog_status,
    )
    similar_incidents = await memory.query_similar(
        service=resolved_service.device_service_id,
        sample_messages=summary.sample_messages,
    )
    latest_user_message = receptionist_message
    if not latest_user_message and payload.messages:
        latest_user_message = _message_text(payload.messages[-1])

    technical_work_order = {
        "user_message": latest_user_message,
        "service_alias": payload.service_alias or resolved_service.requested_name,
        "resolved_service": {
            "friendly_name": resolved_service.friendly_name,
            "device_service_id": resolved_service.device_service_id,
        },
        "technical_context": technical_context,
        "graylog_logs": summary.sample_logs or [],
        "frieren_memory": [
            {
                "service": incident.service,
                "root_cause": incident.root_cause,
                "fix": incident.fix,
            }
            for incident in similar_incidents
        ],
    }
    logger.info("[SLACK CHAT] Technical work order: %s", technical_work_order)
    analysis_context = {
        **technical_context,
        "work_order_type": "Technical Work Order",
        "user_message": latest_user_message,
    }
    reply = await mentor.diagnose_logs(
        service=resolved_service.device_service_id,
        service_name=resolved_service.friendly_name,
        primary_stream=resolved_service.primary_stream,
        search_query=search_query,
        env=env,
        technical_context=analysis_context,
        error_count=summary.error_count,
        sample_messages=summary.sample_messages,
        graylog_logs=summary.sample_logs or [],
        similar_incidents=similar_incidents,
    )

    return ChatResponse(
        action="TECHNICAL_ANALYSIS" if receptionist_message else "FETCH_LOGS",
        service=resolved_service.friendly_name,
        device_service_id=resolved_service.device_service_id,
        env=env,
        window_seconds=window_seconds,
        reply=reply,
    )


@router.post("/diagnostics", response_model=DiagnosticsResponse)
async def thera_diagnostics(payload: DiagnosticsRequest, request: Request) -> DiagnosticsResponse:
    services = get_services(request)
    mentor = services.mentor

    from theraops_backend.diagnostics.orchestrator import DiagnosticsOrchestrator

    orchestrator = DiagnosticsOrchestrator()

    device_id = payload.device_id.strip()
    window_seconds = int(payload.window_seconds)

    try:
        started = time.time()
    except Exception:
        started = None

    try:
        result = await orchestrator.run(
            device_id=device_id,
            services=services,
            mentor=mentor,
            window_seconds=window_seconds,
        )
    except Exception as exc:
        logger.exception("[SLACK DIAGNOSTICS] Orchestration failed: %s", exc)
        raise HTTPException(status_code=500, detail="Diagnostics orchestration failed") from exc

    ev = result.evidence
    device_status = ev.device_status.status if ev.device_status else "unknown"
    last_seen = ev.device_status.last_seen.isoformat() if ev.device_status and ev.device_status.last_seen else None

    telemetry = ev.telemetry_health
    disconnect_events = [
        {
            "kind": e.kind,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "device_id": e.device_id,
        }
        for e in (ev.disconnect_events or [])[:10]
    ]
    active_alerts = [
        {
            "severity": a.severity,
            "title": a.title,
            "detail": a.detail,
        }
        for a in (ev.active_alerts or [])[:10]
    ]

    try:
        if started is not None:
            logger.info("[SLACK DIAGNOSTICS] device=%s status=%s latency=%.2fs providers_errors=%d",
                        device_id, device_status, (time.time() - started), len(ev.errors))
    except Exception:
        pass

    return DiagnosticsResponse(
        device_id=result.device_id,
        device_label=result.device_label,
        device_status=device_status,
        last_seen=last_seen,
        firmware_version=telemetry.firmware_version if telemetry else None,
        connectivity_state=ev.device_status.connectivity_state if ev.device_status else None,
        telemetry_health=telemetry.summary if telemetry else None,
        sensor_health=telemetry.sensor_health if telemetry else None,
        battery_power=telemetry.battery_power if telemetry else None,
        disconnect_reconnect_events=disconnect_events,
        active_alerts=active_alerts,
        likely_issue_cause=result.likely_issue_cause,
        recommended_troubleshooting=result.recommended_troubleshooting,
        ai_summary=result.ai_summary,
        provider_errors=ev.errors or [],
    )


@router.get("/api/issues/network-summary")
async def network_summary(request: Request, source: str = "graylog") -> dict:
    """Return a Trinity network summary by fuzzy-joining Events, Logs, and Heartbeats.

    This uses the Rosetta Stone identity resolver to join disparate ID fields.
    """
    services = get_services(request)
    from theraops_backend.logic.trinity_fetcher import fetch_trinity_summary

    try:
        payload = await fetch_trinity_summary(services, window_seconds=24 * 3600)
    except Exception as exc:
        logger.exception("Graylog Streams fetch failed: %s", exc)
        payload = {"networks": {}, "raw_data": []}

    if payload.get("status") == "error":
        return payload

    return payload

    
    
