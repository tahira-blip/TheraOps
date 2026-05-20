from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from theraops_backend.diagnostics.models import DeviceStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceStatusInput:
    device_id: str


class DeviceStatusProvider:
    async def get_status(self, device_id: str, *, services: Any, rosetta: Any) -> DeviceStatus:
        raise NotImplementedError


class GraylogDeviceStatusProvider(DeviceStatusProvider):
    async def get_status(self, device_id: str, *, services: Any, rosetta: Any) -> DeviceStatus:
        """
        Best-effort status classification using heartbeats + stream_stopped events.
        """
        graylog = services.graylog
        now = datetime.now(timezone.utc)

        identity = rosetta.resolve_device_id(device_id) if rosetta else None

        # heartbeat: last_seen
        try:
            graylog_service_id = (
                identity.service_id
                if identity and getattr(identity, "service_id", None)
                else identity.device_id
                if identity and identity.device_id
                else str(device_id)
            )
            heartbeats = await graylog.fetch_latest_heartbeats(service=graylog_service_id)
        except Exception as exc:
            logger.exception("[diagnostics] heartbeat fetch failed: %s", exc)
            heartbeats = []

        last_seen = None
        if heartbeats:
            # heartbeats already sorted by device id; find matching
            for hb in heartbeats:
                if str(hb.device_device_id) == str(device_id) or (
                    identity and hb.device_device_id == identity.device_id
                ):
                    last_seen = hb.last_seen
                    break
            if not last_seen:
                last_seen = max((hb.last_seen for hb in heartbeats if hb.last_seen), default=None)

        # disconnect/reconnect-ish evidence from stream_stopped
        try:
            stopped_events = await graylog.fetch_stream_stopped_events(
                service=graylog_service_id,
                window_seconds=24 * 3600,
            )
        except Exception as exc:
            logger.exception("[diagnostics] stream_stopped fetch failed: %s", exc)
            stopped_events = []

        status: str
        connectivity_state = None

        if last_seen is None:
            try:
                lookup_id = identity.device_id if identity and identity.device_id else str(device_id)
                device_events = await graylog.fetch_device_events(
                    device_id=lookup_id,
                    window_seconds=24 * 3600,
                    limit=20,
                )
            except Exception as exc:
                logger.exception("[diagnostics] device event fetch failed: %s", exc)
                device_events = []

            if device_events:
                last_seen = max((event.timestamp for event in device_events if event.timestamp), default=None)
                heartbeat_events = [
                    event
                    for event in device_events
                    if event.stream in {"production-heartbeat-events", "production-heartbeat-fleet"}
                ]
                if heartbeat_events and last_seen is not None:
                    minutes = (now - last_seen).total_seconds() / 60.0
                    if minutes <= 5:
                        status = "online"
                        connectivity_state = "connected"
                    elif minutes <= 15:
                        status = "degraded"
                        connectivity_state = "intermittent"
                    else:
                        status = "offline"
                        connectivity_state = "disconnected"
                else:
                    status = "degraded"
                    connectivity_state = "event_seen_no_heartbeat"
            else:
                status = "unknown"
        else:
            delta = now - last_seen
            minutes = delta.total_seconds() / 60.0

            # heuristic thresholds
            if minutes <= 5:
                status = "online"
                connectivity_state = "connected"
            elif minutes <= 15:
                status = "degraded"
                connectivity_state = "intermittent"
            else:
                status = "offline"
                connectivity_state = "disconnected"

        # If we see recent stream_stopped close to last_seen, override to degraded
        # (device recently stopped a stream but has some recent activity)
        if status in {"online", "degraded"} and stopped_events:
            # if there is a stopped event in last 30 minutes, call it degraded
            for ev in stopped_events[:50]:
                if ev.timestamp and (now - ev.timestamp).total_seconds() <= 30 * 60:
                    status = "degraded"
                    connectivity_state = "unstable"
                    break

        return DeviceStatus(status=status, last_seen=last_seen, connectivity_state=connectivity_state)
