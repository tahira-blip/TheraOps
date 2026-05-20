from __future__ import annotations

import json
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


class GraylogModel(BaseModel):
    """Base model for Graylog payloads that include many non-diagnostic fields."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def _json_load_if_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped:
        return ""

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _coerce_model_list(value: Any) -> list[Any]:
    value = _json_load_if_string(value)

    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [_json_load_if_string(item) for item in value]
    if isinstance(value, tuple | set):
        return [_json_load_if_string(item) for item in value]
    if isinstance(value, dict):
        return [value]

    return []


def _coerce_string_list(value: Any) -> list[str]:
    value = _json_load_if_string(value)

    if value is None or value == "":
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if item is not None and str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]

    return [str(value)]


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid integers")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError("value must be an integer")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("empty string is not a valid integer")
        numeric_value = float(stripped)
        if numeric_value.is_integer():
            return int(numeric_value)
        raise ValueError("value must be an integer")

    raise ValueError("value must be an integer")


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid floats")
    return float(value)


class AppletStatus(GraylogModel):
    """Applet state parsed from the Graylog device_hub_applets JSON array."""

    name: str
    status: str
    uptime: int

    @field_validator("uptime", mode="before")
    @classmethod
    def _validate_uptime(cls, value: Any) -> int:
        return _coerce_int(value)


class SensorStatus(GraylogModel):
    """Sensor state from device_device_sensors payloads or ze_0008 sensor events."""

    sensor_id: str
    status: str = Field(validation_alias=AliasChoices("status", "sensor_status"))
    is_alive: bool = False
    path: str | None = None
    event_code: str | None = None


class SystemVitals(GraylogModel):
    """Heartbeat vitals from production-heartbeat-events."""

    cpu_avg: float = Field(validation_alias=AliasChoices("cpu_avg", "device_cpu_avg_percent"))
    memory_used_pct: float = Field(
        validation_alias=AliasChoices("memory_used_pct", "device_memory_used_percent")
    )
    storage_used_pct: float = Field(
        validation_alias=AliasChoices("storage_used_pct", "device_storage_used_percent")
    )
    uptime_seconds: int = Field(validation_alias=AliasChoices("uptime_seconds", "device_uptime_seconds"))

    @field_validator("cpu_avg", "memory_used_pct", "storage_used_pct", mode="before")
    @classmethod
    def _validate_percent(cls, value: Any) -> float:
        return _coerce_float(value)

    @field_validator("uptime_seconds", mode="before")
    @classmethod
    def _validate_uptime_seconds(cls, value: Any) -> int:
        return _coerce_int(value)


class DiagnosticResult(GraylogModel):
    """Graylog-aligned diagnostic wrapper for Device, Applet, and Sensor hierarchy data."""

    device_id: str
    serial: str | None = Field(default=None, validation_alias=AliasChoices("serial", "device_device_serial_id"))
    network_code: str | None = None
    site_id: str | None = None
    identity_name: str | None = None

    status_label: str = Field(default="unknown", validation_alias=AliasChoices("status_label", "device_statusx"))
    alerts: list[str] = Field(default_factory=list, validation_alias=AliasChoices("alerts", "device_alerts"))
    is_edgeless: bool = False

    vitals: SystemVitals | None = None
    applets: list[AppletStatus] = Field(
        default_factory=list,
        validation_alias=AliasChoices("applets", "device_hub_applets"),
    )
    sensors: list[SensorStatus] = Field(
        default_factory=list,
        validation_alias=AliasChoices("sensors", "device_device_sensors"),
    )

    last_seen: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("last_seen", "timestamp", "ts_epoch"),
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_nested_components(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)

        if data.get("vitals") is None:
            graylog_vital_fields = (
                "device_cpu_avg_percent",
                "device_memory_used_percent",
                "device_storage_used_percent",
                "device_uptime_seconds",
            )
            internal_vital_fields = ("cpu_avg", "memory_used_pct", "storage_used_pct", "uptime_seconds")
            if all(data.get(field) is not None for field in graylog_vital_fields):
                data["vitals"] = data
            elif all(data.get(field) is not None for field in internal_vital_fields):
                data["vitals"] = data

        has_sensor_event_fields = any(
            data.get(field) is not None
            for field in ("sensor_id", "sensor_status", "is_alive", "path", "event_code")
        )
        if data.get("sensors") is None and data.get("device_device_sensors") is None and has_sensor_event_fields:
            data["sensors"] = [data]

        return data

    @field_validator("alerts", mode="before")
    @classmethod
    def _validate_alerts(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)

    @field_validator("status_label", mode="before")
    @classmethod
    def _validate_status_label(cls, value: Any) -> str:
        if value is None or str(value).strip() == "":
            return "unknown"
        return str(value)

    @field_validator("applets", "sensors", mode="before")
    @classmethod
    def _validate_hierarchy_lists(cls, value: Any) -> list[Any]:
        return _coerce_model_list(value)

    @field_validator("last_seen", mode="before")
    @classmethod
    def _validate_last_seen(cls, value: Any) -> Any:
        value = _json_load_if_string(value)
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, int | float):
            epoch = float(value)
            if epoch > 10_000_000_000:
                epoch = epoch / 1000
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                epoch = float(stripped)
                if epoch > 10_000_000_000:
                    epoch = epoch / 1000
                return datetime.fromtimestamp(epoch, tz=timezone.utc)
            except ValueError:
                return stripped

        return value

    @model_validator(mode="after")
    def _derive_is_edgeless(self) -> DiagnosticResult:
        if self.identity_name is not None:
            self.is_edgeless = self.identity_name.strip().casefold() == "edgeless"
        return self

    @computed_field
    @property
    def summary_emoji(self) -> str:
        return {
            "critical": "🔴",
            "attention": "⚠️",
            "healthy": "✅",
        }.get(self.status_label.strip().casefold(), "")


@dataclass
class DeviceStatus:
    status: str  # online/offline/degraded/unknown
    last_seen: datetime | None
    connectivity_state: str | None = None


@dataclass
class TelemetryHealth:
    summary: str
    firmware_version: str | None = None
    sensor_health: str | None = None
    battery_power: str | None = None
    connectivity: str | None = None


@dataclass
class DisconnectReconnectEvent:
    kind: str  # disconnect/reconnect/stream_stopped/heartbeat
    timestamp: datetime | None
    raw: dict[str, Any] | None = None
    device_id: str | None = None


@dataclass
class ActiveAlert:
    severity: str | None
    title: str
    detail: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class DiagnosticsEvidence:
    device_status: DeviceStatus | None = None
    telemetry_health: TelemetryHealth | None = None
    disconnect_events: list[DisconnectReconnectEvent] = dataclass_field(default_factory=list)
    active_alerts: list[ActiveAlert] = dataclass_field(default_factory=list)
    graylog_status: str = "unknown"  # ok/unreachable/etc
    errors: list[str] = dataclass_field(default_factory=list)


@dataclass
class DeviceDiagnosticsResult:
    device_id: str
    device_label: str | None = None
    evidence: DiagnosticsEvidence = dataclass_field(default_factory=DiagnosticsEvidence)
    likely_issue_cause: str = "Insufficient evidence to determine likely issue cause."
    recommended_troubleshooting: list[str] = dataclass_field(default_factory=list)
    ai_summary: str | None = None


__all__ = [
    "ActiveAlert",
    "AppletStatus",
    "DeviceDiagnosticsResult",
    "DeviceStatus",
    "DiagnosticResult",
    "DiagnosticsEvidence",
    "DisconnectReconnectEvent",
    "SensorStatus",
    "SystemVitals",
    "TelemetryHealth",
]
