from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from theraops_backend.diagnostics.models import TelemetryHealth

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelemetryHealthInput:
    device_id: str


class TelemetryHealthProvider:
    async def get_health(self, device_id: str, *, services: Any, rosetta: Any) -> TelemetryHealth:
        raise NotImplementedError


class GraylogTelemetryHealthProvider(TelemetryHealthProvider):
    async def get_health(self, device_id: str, *, services: Any, rosetta: Any) -> TelemetryHealth:
        """
        Best-effort telemetry health using available Graylog fields.
        If fields are missing in the payload, returns partially populated TelemetryHealth.
        """
        graylog = services.graylog
        safe_env = None

        identity = rosetta.resolve_device_id(device_id) if rosetta else None
        graylog_service_id = (
            identity.service_id
            if identity and getattr(identity, "service_id", None)
            else identity.device_id
            if identity and identity.device_id
            else str(device_id)
        )

        now = datetime.now(timezone.utc)

        # Try to pull recent device telemetry-like logs/events that may contain fields.
        # We keep query broad to remain extensible across telemetry providers.
        # Best-effort Lucene matching across possible ID types (quoted vs numeric) and possible field names.
        # This avoids the "Empty Bag" failure mode when Graylog stores IDs as ints or under a different field.
        raw_ids = [str(device_id), str(graylog_service_id)]
        if identity:
            raw_ids.extend(
                str(value)
                for value in (getattr(identity, "device_id", None), getattr(identity, "serial", None))
                if value
            )

        normalized_ids: list[str] = []
        seen_ids: set[str] = set()
        for raw_id in raw_ids:
            value = raw_id.strip()
            if not value:
                continue
            values = [value]
            lowered = value.lower()
            if lowered.startswith("device "):
                values.append(value.split(None, 1)[1].strip())
            if lowered.startswith("device-"):
                values.append(value.split("-", 1)[1].strip())
            for item in values:
                if item and item not in seen_ids:
                    seen_ids.add(item)
                    normalized_ids.append(item)

        candidates = []
        for value in normalized_ids:
            candidates.extend(
                [
                    f'device_service_id:"{value}"',
                    f'device_device_service_id:"{value}"',
                    f'device_id:"{value}"',
                    f'device_device_id:"{value}"',
                    f'device_serial_id:"{value}"',
                    f'device_device_serial_id:"{value}"',
                ]
            )
            if value.isdigit():
                candidates.extend(
                    [
                        f"device_id:{value}",
                        f"device_device_id:{value}",
                        f'device_service_id:"device-{value}"',
                    ]
                )

        seen_candidates: set[str] = set()
        candidates = [
            candidate
            for candidate in candidates
            if not (candidate in seen_candidates or seen_candidates.add(candidate))
        ]

        messages = []
        last_exc: Exception | None = None
        query = "(" + " OR ".join(candidates) + ")" if candidates else f'device_id:"{device_id}"'
        for query in [query]:
            try:
                result = await graylog.search_relative(
                    query=query,
                    window_seconds=6 * 3600,
                    limit=200,
                    # Note: keep fields broad; we can’t assume exact schema evolution.
                    fields=[
                        "timestamp",
                        "device_device_service_id",
                        "device_service_id",
                        "device_id",
                        "device_device_id",
                        "device_serial_id",
                        "device_device_serial_id",
                        "device_message",
                        "firmware_version",
                        "telemetry_status",
                        "sensor_health",
                        "device_cpu_avg_percent",
                        "device_memory_used_percent",
                        "device_storage_used_percent",
                        "device_uptime_seconds",
                        "battery_level",
                        "battery_status",
                        "power_state",
                        "connectivity_state",
                        "env",
                        "environment",
                        "stage",
                    ],
                )
                messages = result.messages or []
                # If we got any telemetry-like payload, stop early.
                if messages:
                    break
            except Exception as exc:
                last_exc = exc
                logger.debug("[diagnostics] telemetry query failed (%s): %s", query, exc)

        # If no queries returned messages, return unavailable best-effort.
        if not messages:
            if last_exc is not None:
                logger.exception("[diagnostics] telemetry health fetch failed: %s", last_exc)
                summary = "Telemetry health unavailable (Graylog fetch failed)."
            else:
                summary = "Telemetry health unavailable (no recent telemetry-like Graylog payload)."

            return TelemetryHealth(
                summary=summary,
                firmware_version=None,
                sensor_health=None,
                battery_power=None,
                connectivity=None,
            )


        firmware_versions = set()
        sensor_healths = set()
        battery_states = set()
        connectivity_states = set()

        latest_ts = None
        for msg in messages:
            # timestamps may be ISO strings; reuse GraylogClient parser indirectly by inspecting raw field
            ts_raw = msg.get("timestamp")
            # tolerate failures; best-effort only
            if ts_raw and not latest_ts:
                try:
                    # GraylogClient parses in its own method; keep simple here
                    latest_ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                except Exception:
                    latest_ts = None

            fw = msg.get("firmware_version") or msg.get("firmware") or msg.get("fw_version")
            sh = msg.get("sensor_health") or msg.get("telemetry_status")
            batt = msg.get("battery_level") or msg.get("battery_status") or msg.get("power_state")
            conn = msg.get("connectivity_state") or msg.get("device_connectivity_state")

            if fw:
                firmware_versions.add(str(fw).strip())
            if sh:
                sensor_healths.add(str(sh).strip())
            if batt:
                battery_states.add(str(batt).strip())
            if conn:
                connectivity_states.add(str(conn).strip())

        firmware_version = next(iter(firmware_versions)) if firmware_versions else None
        sensor_health = next(iter(sensor_healths)) if sensor_healths else None
        battery_power = next(iter(battery_states)) if battery_states else None
        connectivity = next(iter(connectivity_states)) if connectivity_states else None

        # Telemetry health summary heuristics:
        # - if we have sensor_health or telemetry_status, we can reflect it
        # - otherwise show "limited signal" if no fields found but logs exist
        if sensor_health:
            summary = f"Telemetry appears present: {sensor_health}."
        elif messages:
            summary = "Telemetry logs present, but health fields were not found in Graylog payload."
        else:
            summary = "Telemetry health unavailable (no recent telemetry-like Graylog payload)."

        return TelemetryHealth(
            summary=summary,
            firmware_version=firmware_version,
            sensor_health=sensor_health,
            battery_power=battery_power,
            connectivity=connectivity,
        )
