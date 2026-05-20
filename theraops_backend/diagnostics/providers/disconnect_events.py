from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from theraops_backend.diagnostics.models import DisconnectReconnectEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DisconnectEventsInput:
    device_id: str
    window_seconds: int = 24 * 3600


class GraylogDisconnectReconnectEventsProvider:
    """
    Best-effort event extraction from Graylog streams.
    - stream_stopped: treat as disconnect-ish signal
    - heartbeat: treat as reconnect-ish/continued connectivity signal

    Note: Graylog here only exposes stream_stopped via existing GraylogClient helpers.
    For heartbeat, we use latest heartbeats and infer reconnection continuity.
    """

    async def get_events(
        self,
        device_id: str,
        *,
        services: Any,
        rosetta: Any,
        window_seconds: int,
    ) -> list[DisconnectReconnectEvent]:
        graylog = services.graylog

        identity = rosetta.resolve_device_id(device_id) if rosetta else None
        graylog_service_id = (
            identity.service_id
            if identity and getattr(identity, "service_id", None)
            else identity.device_id
            if identity and identity.device_id
            else str(device_id)
        )

        # stream_stopped events (disconnect-ish)
        stopped_events_raw: list[Any] = []
        try:
            stopped_events_raw = await graylog.fetch_stream_stopped_events(
                service=graylog_service_id,
                window_seconds=window_seconds,
                limit=200,
            )
        except Exception as exc:
            logger.exception("[diagnostics] fetch_stream_stopped_events failed: %s", exc)
            stopped_events_raw = []

        stopped_events: list[DisconnectReconnectEvent] = [
            DisconnectReconnectEvent(
                kind="disconnect",
                timestamp=ev.timestamp,
                raw=ev.raw if hasattr(ev, "raw") else None,
                device_id=getattr(ev, "device_device_id", None),
            )
            for ev in stopped_events_raw
        ]

        if not stopped_events:
            try:
                device_error_events = await graylog.fetch_device_events(
                    device_id=device_id,
                    window_seconds=window_seconds,
                    limit=200,
                    streams=["production-device-error-events", None],
                )
            except Exception as exc:
                logger.exception("[diagnostics] fetch_device_events for device errors failed: %s", exc)
                device_error_events = []

            stopped_events = [
                DisconnectReconnectEvent(
                    kind=event.event_code or "device_error",
                    timestamp=event.timestamp,
                    raw=event.raw,
                    device_id=event.device_id,
                )
                for event in device_error_events
            ]

        # heartbeat continuity (reconnect-ish inferred)
        # We'll pull recent heartbeats for the same device and infer "reconnect" when a heartbeat arrives
        # after at least one disconnect event.
        heartbeats_raw: list[Any] = []
        try:
            # GraylogClient has fetch_latest_heartbeats only; this gives last_seen, not a full sequence.
            # We'll still use it as a "reconnect/healthy" evidence at end of window.
            heartbeats_raw = await graylog.fetch_latest_heartbeats(
                service=graylog_service_id,
                window_seconds=window_seconds,
                limit=500,
            )
        except Exception as exc:
            logger.exception("[diagnostics] fetch_latest_heartbeats failed: %s", exc)
            heartbeats_raw = []

        # If we have any heartbeat within window and we saw disconnects, add a reconnect evidence.
        last_seen = None
        for hb in heartbeats_raw:
            if str(getattr(hb, "device_device_id", "")) == str(device_id):
                last_seen = getattr(hb, "last_seen", None)
                break
        if last_seen is None and heartbeats_raw:
            last_seen = getattr(heartbeats_raw[0], "last_seen", None)

        reconnect_events: list[DisconnectReconnectEvent] = []
        if last_seen is not None and stopped_events:
            reconnect_events.append(
                DisconnectReconnectEvent(
                    kind="reconnect",
                    timestamp=last_seen,
                    raw=None,
                    device_id=device_id,
                )
            )

        # Return most recent first (limited)
        combined = sorted([*reconnect_events, *stopped_events], key=lambda e: e.timestamp or datetime.min, reverse=True)
        return combined[:20]
