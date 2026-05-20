from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from theraops_backend.diagnostics.models import ActiveAlert

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertsProvider:
    """
    Interface marker; kept simple for now. Future implementations can replace the
    fake provider with real alert/incident telemetry sources.
    """

    async def get_active_alerts(self, device_id: str, *, services: Any) -> list[ActiveAlert]:
        raise NotImplementedError


@dataclass(frozen=True)
class GraylogAlertsProvider(AlertsProvider):
    async def get_active_alerts(self, device_id: str, *, services: Any) -> list[ActiveAlert]:
        graylog = services.graylog
        try:
            events = await graylog.fetch_device_events(
                device_id=device_id,
                window_seconds=24 * 3600,
                limit=20,
            )
        except Exception as exc:
            logger.exception("[diagnostics] active alert event fetch failed: %s", exc)
            return []

        alerts: list[ActiveAlert] = []
        alert_events = [
            event
            for event in events
            if event.event_code or event.stream in {"production-non-session-events", "production-device-error-events"}
        ]
        for event in alert_events[:6]:
            code = event.event_code or "unknown"
            title = event.event_name or _event_title(code)
            severity = _severity_for_event(code)
            alerts.append(
                ActiveAlert(
                    severity=severity,
                    title=f"{title}: {code}",
                    detail=_event_detail(event.raw),
                    raw=event.raw,
                )
            )
        return alerts


def _event_title(event_code: str) -> str:
    return {
        "con_0002": "Device Offline",
        "pc_0010": "Device Offline",
        "pc_0008": "Sensor Offline",
        "scon_0002": "Sensor Offline",
        "samdt_0006": "Sensor Offline",
        "pc_0012": "Connection Error",
        "samdt_0003": "Connection Error",
    }.get(event_code, "Graylog Event")


def _severity_for_event(event_code: str) -> str:
    if event_code in {"con_0002", "pc_0010", "pc_0008", "scon_0002", "samdt_0006"}:
        return "critical"
    if event_code in {"pc_0012", "samdt_0003"}:
        return "high"
    return "info"


def _event_detail(raw: dict[str, Any]) -> str:
    device = raw.get("device_id") or raw.get("device_service_id") or "unknown"
    identity = raw.get("identity_name") or "unknown identity"
    network = raw.get("network_code") or raw.get("network_id") or "unknown network"
    # stream = raw.get("_stream") or "all streams"
    return f"Graylog event for device `{device}` on `{identity}` in network `{network}`."


FakeAlertsProvider = GraylogAlertsProvider
