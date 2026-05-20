from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServiceRegistryEntry:
    friendly_name: str
    device_service_id: str
    device_service_name: str
    primary_stream: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ResolvedService:
    requested_name: str
    friendly_name: str
    device_service_id: str
    device_service_name: str
    primary_stream: str


DEVICE_SERVICE_REGISTRY: dict[str, dict[str, str]] = {
    "api": {"id": "api", "name": "TheraOps API"},
    "backend": {"id": "api", "name": "TheraOps API"},
    "thera-api": {"id": "api", "name": "TheraOps API"},
    "billing": {"id": "billing", "name": "Billing Service"},
    "payments": {"id": "billing", "name": "Billing Service"},
    "billing-api": {"id": "billing", "name": "Billing Service"},
    "viana": {"id": "vianapulse", "name": "Viana Edge Grid Pulse"},
    "vianapulse": {"id": "vianapulse", "name": "Viana Edge Grid Pulse"},
    "vp": {"id": "vianapulse", "name": "Viana Edge Grid Pulse"},
    "worker": {"id": "worker", "name": "Background Worker"},
    "jobs": {"id": "worker", "name": "Background Worker"},
    "queue": {"id": "worker", "name": "Background Worker"},
    "task-runner": {"id": "worker", "name": "Background Worker"},
}


SERVICE_REGISTRY: dict[str, ServiceRegistryEntry] = {
    "api": ServiceRegistryEntry(
        friendly_name="api",
        device_service_id="api",
        device_service_name="TheraOps API",
        primary_stream="application-api",
        aliases=("backend", "thera-api"),
    ),
    "billing": ServiceRegistryEntry(
        friendly_name="billing",
        device_service_id="billing",
        device_service_name="Billing Service",
        primary_stream="billing",
        aliases=("payments", "billing-api"),
    ),
    "vianapulse": ServiceRegistryEntry(
        friendly_name="vianapulse",
        device_service_id="vianapulse",
        device_service_name="Viana Edge Grid Pulse",
        primary_stream="vianapulse",
        aliases=("vp", "viana"),
    ),
    "worker": ServiceRegistryEntry(
        friendly_name="worker",
        device_service_id="worker",
        device_service_name="Background Worker",
        primary_stream="background-worker",
        aliases=("jobs", "queue", "task-runner"),
    ),
}


class ServiceRegistry:
    def __init__(
        self,
        entries: dict[str, ServiceRegistryEntry] | None = None,
        allowed_service_ids: list[str] | None = None,
    ) -> None:
        self._entries = entries or SERVICE_REGISTRY
        self._allowed_service_ids = set(allowed_service_ids or [])
        self._normalized_allowed_service_ids = {
            self._normalize(service_id) for service_id in self._allowed_service_ids
        }
        self._index: dict[str, ServiceRegistryEntry] = {}

        for key, entry in self._entries.items():
            names = {key, entry.friendly_name, entry.device_service_id, *entry.aliases}
            for name in names:
                self._index[self._normalize(name)] = entry

    def resolve(self, requested_name: str) -> ResolvedService | None:
        normalized = self._normalize(requested_name)
        entry = self._index.get(normalized)

        if entry:
            if (
                self._normalized_allowed_service_ids
                and self._normalize(entry.device_service_id) not in self._normalized_allowed_service_ids
            ):
                return None
            return ResolvedService(
                requested_name=requested_name,
                friendly_name=entry.friendly_name,
                device_service_id=entry.device_service_id,
                device_service_name=entry.device_service_name,
                primary_stream=entry.primary_stream,
            )

        if normalized in self._normalized_allowed_service_ids:
            return ResolvedService(
                requested_name=requested_name,
                friendly_name=requested_name,
                device_service_id=requested_name,
                device_service_name=requested_name,
                primary_stream="default",
            )

        return None

    def valid_names(self) -> list[str]:
        names = {
            entry.friendly_name
            for entry in self._entries.values()
            if not self._normalized_allowed_service_ids
            or self._normalize(entry.device_service_id) in self._normalized_allowed_service_ids
        }
        names.update(self._allowed_service_ids)
        return sorted(names)

    def _normalize(self, value: str) -> str:
        return value.strip().lower()
