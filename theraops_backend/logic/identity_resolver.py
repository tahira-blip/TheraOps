from __future__ import annotations

from typing import Dict, Any
from dataclasses import dataclass
import logging

import httpx

logger = logging.getLogger(__name__)

# STREAM DEFINITIONS
STREAM_NONSESSION_EVENTS = '68ec582d599baa79678aec0f'
STREAM_DEVICE_ERRORS = '698af80935d09206f2516092'
STREAM_HEARTBEAT_FLEET = '698d665935d09206f255e760'


@dataclass
class DeviceIdentity:
    device_id: str | None
    service_id: str | None
    serial: str | None
    name: str | None
    network_code: str | None
    network_id: int | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "service_id": self.service_id,
            "serial": self.serial,
            "display_name": self.name,
            "network_code": self.network_code,
            "network_id": self.network_id,
        }


class RosettaStone:
    """Builds a mapping from available heartbeat messages so other streams
    can be joined even when they use different ID fields.

    Usage:
        rs = RosettaStone()
        await rs.build(graylog, window_seconds=3600)
        lookup = rs.lookup_map
    """

    def __init__(self) -> None:
        self.lookup_map: dict[str, DeviceIdentity] = {}
        self.identities: dict[str, DeviceIdentity] = {}
        self.network_code_by_id: dict[int, str] = {}
        self.device_serial_by_id: dict[int, str] = {}

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value in (None, "", "0", 0):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def build(self, graylog, window_seconds: int = 3600) -> None:
        try:
            result = await graylog.search_relative(
                query="*",
                window_seconds=window_seconds,
                fields=[
                    "timestamp",
                    "device_id",
                    "device_service_id",
                    "device_serial_id",
                    "device_device_id",
                    "device_service_name",
                    "device_device_serial_id",
                    "device_name",
                    "network_id",
                    "network_code",
                ],
                limit=2000,
                stream=STREAM_HEARTBEAT_FLEET,
            )
        except httpx.ReadTimeout:
            raise
        except Exception as exc:
            logger.exception("Failed to fetch heartbeats for RosettaStone: %s", exc)
            return

        for msg in result.messages:
            dev_id = msg.get("device_device_id") or msg.get("device_id")
            service_id = msg.get("device_service_id")
            serial = msg.get("device_device_serial_id") or msg.get("device_serial_id")
            name = msg.get("device_name") or msg.get("device_service_name") or None
            network = msg.get("network_code") or None
            network_id = self._as_int(msg.get("network_id"))

            identity = DeviceIdentity(
                device_id=str(dev_id) if dev_id else None,
                service_id=str(service_id) if service_id else None,
                serial=str(serial) if serial else None,
                name=str(name) if name else None,
                network_code=str(network) if network else None,
                network_id=network_id,
            )

            if network_id is not None and identity.network_code:
                self.network_code_by_id[network_id] = identity.network_code
            numeric_device_id = self._as_int(dev_id)
            if numeric_device_id is not None and identity.serial:
                self.device_serial_by_id[numeric_device_id] = identity.serial

            # index by several possible keys, including Graylog field-qualified keys
            if identity.device_id:
                self.lookup_map[identity.device_id] = identity
                self.lookup_map[f"device_id:{identity.device_id}"] = identity
                self.lookup_map[f"device_device_id:{identity.device_id}"] = identity
            if identity.service_id:
                self.lookup_map[identity.service_id] = identity
                self.lookup_map[f"device_service_id:{identity.service_id}"] = identity
            if identity.serial:
                self.lookup_map[identity.serial] = identity
                self.lookup_map[f"device_serial_id:{identity.serial}"] = identity
                self.lookup_map[f"device_device_serial_id:{identity.serial}"] = identity
            if identity.name:
                self.lookup_map[identity.name] = identity

            # also keep canonical list keyed by device_id or serial
            key = identity.device_id or identity.service_id or identity.serial or identity.name or None
            if key:
                self.identities[key] = identity

        logger.info("RosettaStone built: %d identities", len(self.identities))

    def resolve(self, key: str) -> DeviceIdentity | None:
        if not key:
            return None
        for lookup_key in self._lookup_keys(key):
            match = self.lookup_map.get(lookup_key)
            if match:
                return match
        return None

    def resolve_device_id(self, device_id: Any) -> DeviceIdentity | None:
        if device_id in (None, "", "0", 0):
            return None
        return self.resolve(str(device_id))

    def _lookup_keys(self, value: Any) -> list[str]:
        raw = str(value).strip()
        if not raw:
            return []

        keys = [raw]
        lowered = raw.lower()
        if ":" in raw:
            field, field_value = raw.split(":", 1)
            field = field.strip()
            field_value = field_value.strip().strip('"')
            if field_value:
                keys.append(field_value)
                keys.append(f"{field}:{field_value}")
                if field == "serial":
                    keys.append(f"device_serial_id:{field_value}")
                    keys.append(f"device_device_serial_id:{field_value}")
        elif " " in raw:
            field, field_value = raw.split(None, 1)
            field = field.strip()
            field_value = field_value.strip().strip('"')
            if field in {
                "device_id",
                "device_device_id",
                "device_service_id",
                "device_serial_id",
                "device_device_serial_id",
                "serial",
            } and field_value:
                keys.append(field_value)
                keys.append(f"{field}:{field_value}")
                if field == "serial":
                    keys.append(f"device_serial_id:{field_value}")
                    keys.append(f"device_device_serial_id:{field_value}")
        if lowered.startswith("device "):
            numeric = raw.split(None, 1)[1].strip()
            keys.extend([numeric, f"device_id:{numeric}", f"device_device_id:{numeric}", f"device_service_id:device-{numeric}"])
        if lowered.startswith("device-"):
            numeric = raw.split("-", 1)[1].strip()
            keys.extend([numeric, f"device_id:{numeric}", f"device_device_id:{numeric}", f"device_service_id:{raw}"])

        for key in list(keys):
            keys.extend(
                [
                    f"device_service_id:{key}",
                    f"device_device_id:{key}",
                    f"device_id:{key}",
                    f"device_serial_id:{key}",
                    f"device_device_serial_id:{key}",
                ]
            )

        seen: set[str] = set()
        return [key for key in keys if key and not (key in seen or seen.add(key))]

    def network_code_for(self, network_id: Any, fallback: Any = None) -> str:
        numeric_network_id = self._as_int(network_id)
        if numeric_network_id is not None:
            mapped = self.network_code_by_id.get(numeric_network_id)
            if mapped:
                return mapped
        if fallback not in (None, ""):
            return str(fallback)
        if numeric_network_id is not None:
            return f"network-{numeric_network_id}"
        return "unknown"
