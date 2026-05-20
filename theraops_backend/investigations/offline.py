from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from theraops_backend.core.service_registry import ServiceRegistry

if TYPE_CHECKING:
    from theraops_backend.interface.flamme_mentor import FlammeMentor
    from theraops_backend.monitoring.fern_watcher import DeviceHeartbeat, GraylogClient, StreamStoppedEvent

logger = logging.getLogger(__name__)

DEVICE_ERROR_STREAM = "production-device-error-events"
HEARTBEAT_STREAM = "production-heartbeat-fleet"
GRACEFUL_WINDOW_SECONDS = 3600
HEARTBEAT_WINDOW_SECONDS = 3600
STALE_AFTER_SECONDS = 300


@dataclass(frozen=True)
class OfflineDeviceFinding:
    device_device_id: str
    last_seen: str | None
    classification: str
    stream_stopped_seen: bool
    stream_stopped_at: str | None


@dataclass(frozen=True)
class OfflineInvestigationResult:
    service_alias: str
    device_service_id: str
    device_service_name: str
    checked_at: str
    graceful_disconnect_count: int
    critical_outage_count: int
    healthy_count: int
    stale_count: int
    stream_stopped_count: int
    graceful_disconnects: list[OfflineDeviceFinding]
    critical_outages: list[OfflineDeviceFinding]
    healthy_devices: list[OfflineDeviceFinding]
    graylog_streams: dict[str, str]
    slack_report: str

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _latest_stream_stops(events: list["StreamStoppedEvent"]) -> dict[str, "StreamStoppedEvent"]:
    latest: dict[str, StreamStoppedEvent] = {}
    for event in events:
        current = latest.get(event.device_device_id)
        if current is None:
            latest[event.device_device_id] = event
            continue
        if event.timestamp and (current.timestamp is None or event.timestamp > current.timestamp):
            latest[event.device_device_id] = event
    return latest


def _is_stale(heartbeat: "DeviceHeartbeat", checked_at: datetime) -> bool:
    if heartbeat.last_seen is None:
        return True
    return checked_at - heartbeat.last_seen > timedelta(seconds=STALE_AFTER_SECONDS)


async def investigate_offline(
    service_alias: str,
    *,
    registry: ServiceRegistry,
    graylog: "GraylogClient",
    mentor: "FlammeMentor",
) -> OfflineInvestigationResult:
    resolved_service = registry.resolve(service_alias)
    if not resolved_service:
        valid_names = ", ".join(registry.valid_names())
        raise ValueError(f"Unknown or unmonitored service alias '{service_alias}'. Valid names: {valid_names}")

    checked_at = datetime.now(timezone.utc)
    stream_stopped_events = await graylog.fetch_stream_stopped_events(
        service=resolved_service.device_service_id,
        window_seconds=GRACEFUL_WINDOW_SECONDS,
        stream=DEVICE_ERROR_STREAM,
    )
    latest_stops = _latest_stream_stops(stream_stopped_events)

    heartbeats = await graylog.fetch_latest_heartbeats(
        service=resolved_service.device_service_id,
        window_seconds=HEARTBEAT_WINDOW_SECONDS,
        stream=HEARTBEAT_STREAM,
    )

    graceful_disconnects: list[OfflineDeviceFinding] = []
    critical_outages: list[OfflineDeviceFinding] = []
    healthy_devices: list[OfflineDeviceFinding] = []

    for heartbeat in heartbeats:
        stop_event = latest_stops.get(heartbeat.device_device_id)
        stale = _is_stale(heartbeat, checked_at)
        finding = OfflineDeviceFinding(
            device_device_id=heartbeat.device_device_id,
            last_seen=_iso(heartbeat.last_seen),
            classification=(
                "Maintenance/Graceful"
                if stale and stop_event
                else "CRITICAL: Hard Outage"
                if stale
                else "Healthy"
            ),
            stream_stopped_seen=bool(stop_event),
            stream_stopped_at=_iso(stop_event.timestamp) if stop_event else None,
        )

        if stale and stop_event:
            graceful_disconnects.append(finding)
        elif stale:
            critical_outages.append(finding)
        else:
            healthy_devices.append(finding)

    payload = {
        "service_alias": service_alias,
        "device_service_id": resolved_service.device_service_id,
        "device_service_name": resolved_service.device_service_name,
        "checked_at": _iso(checked_at),
        "graylog_streams": {
            "graceful_check": DEVICE_ERROR_STREAM,
            "heartbeat_check": HEARTBEAT_STREAM,
        },
        "counts": {
            "graceful_disconnects": len(graceful_disconnects),
            "critical_outages": len(critical_outages),
            "healthy_devices": len(healthy_devices),
            "stale_devices": len(graceful_disconnects) + len(critical_outages),
            "stream_stopped_events": len(stream_stopped_events),
        },
        "graceful_disconnects": [asdict(item) for item in graceful_disconnects],
        "critical_outages": [asdict(item) for item in critical_outages],
        "healthy_devices": [asdict(item) for item in healthy_devices],
    }

    logger.info("[OFFLINE INVESTIGATION] %s", payload)
    slack_report = await mentor.format_offline_investigation(payload)

    return OfflineInvestigationResult(
        service_alias=service_alias,
        device_service_id=resolved_service.device_service_id,
        device_service_name=resolved_service.device_service_name,
        checked_at=_iso(checked_at) or "",
        graceful_disconnect_count=len(graceful_disconnects),
        critical_outage_count=len(critical_outages),
        healthy_count=len(healthy_devices),
        stale_count=len(graceful_disconnects) + len(critical_outages),
        stream_stopped_count=len(stream_stopped_events),
        graceful_disconnects=graceful_disconnects,
        critical_outages=critical_outages,
        healthy_devices=healthy_devices,
        graylog_streams={
            "graceful_check": DEVICE_ERROR_STREAM,
            "heartbeat_check": HEARTBEAT_STREAM,
        },
        slack_report=slack_report,
    )
