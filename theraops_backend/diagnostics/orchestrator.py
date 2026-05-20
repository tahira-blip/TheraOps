from __future__ import annotations

import logging
import time
import asyncio
from dataclasses import dataclass
from typing import Any

from theraops_backend.diagnostics.ai import DiagnosticsAI
from theraops_backend.diagnostics.models import (
    ActiveAlert,
    DeviceDiagnosticsResult,
    DiagnosticsEvidence,
)
from theraops_backend.logic.identity_resolver import RosettaStone
from theraops_backend.diagnostics.providers.device_status import GraylogDeviceStatusProvider
from theraops_backend.diagnostics.providers.telemetry_health import GraylogTelemetryHealthProvider
from theraops_backend.diagnostics.providers.disconnect_events import GraylogDisconnectReconnectEventsProvider
from theraops_backend.diagnostics.providers.alerts import FakeAlertsProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiagnosticsOrchestrationResult:
    result: DeviceDiagnosticsResult
    provider_errors: list[str]


class DiagnosticsOrchestrator:
    """
    Concurrently fetch device diagnostics evidence from multiple providers.
    Uses graceful degradation: provider failures do not fail the whole command.
    """

    def __init__(self) -> None:
        self.device_status_provider = GraylogDeviceStatusProvider()
        self.telemetry_health_provider = GraylogTelemetryHealthProvider()
        self.disconnect_events_provider = GraylogDisconnectReconnectEventsProvider()
        self.alerts_provider = FakeAlertsProvider()

    async def run(
        self,
        *,
        device_id: str,
        services: Any,
        mentor: Any,
        window_seconds: int = 24 * 3600,
    ) -> DeviceDiagnosticsResult:
        rosetta = RosettaStone()

        # Build Rosetta mapping (best-effort; never hard fail)
        try:
            await asyncio.wait_for(rosetta.build(services.graylog, window_seconds=window_seconds), timeout=10.0)
        except Exception as exc:
            logger.warning("[diagnostics] RosettaStone build failed: %s", exc)

        device_identity = rosetta.resolve_device_id(device_id) if rosetta else None
        device_label = device_identity.name if device_identity else None

        evidence_errors: list[str] = []

        async def _run_provider(coro_fn, err_label: str):
            t0 = time.perf_counter()
            try:
                value = await asyncio.wait_for(coro_fn(), timeout=8.0)
                logger.info("[diagnostics] provider %s ok (%.2fs)", err_label, time.perf_counter() - t0)
                return value, None
            except Exception as exc:
                logger.exception("[diagnostics] provider %s failed: %s", err_label, exc)
                return None, err_label

        async def device_status_task():
            return await _run_provider(
                lambda: self.device_status_provider.get_status(device_id, services=services, rosetta=rosetta),
                "device_status",
            )

        async def telemetry_task():
            return await _run_provider(
                lambda: self.telemetry_health_provider.get_health(device_id, services=services, rosetta=rosetta),
                "telemetry_health",
            )

        async def disconnect_task():
            return await _run_provider(
                lambda: self.disconnect_events_provider.get_events(
                    device_id,
                    services=services,
                    rosetta=rosetta,
                    window_seconds=window_seconds,
                ),
                "disconnect_events",
            )

        async def alerts_task():
            return await _run_provider(
                lambda: self.alerts_provider.get_active_alerts(device_id, services=services),
                "active_alerts",
            )

        (
            (device_status, device_status_err),
            (telemetry_health, telemetry_err),
            (disconnect_events, disconnect_err),
            (active_alerts, alerts_err),
        ) = await asyncio.gather(
            device_status_task(),
            telemetry_task(),
            disconnect_task(),
            alerts_task(),
            return_exceptions=False,
        )

        if device_status_err:
            evidence_errors.append(device_status_err)
        if telemetry_err:
            evidence_errors.append(telemetry_err)
        if disconnect_err:
            evidence_errors.append(disconnect_err)
        if alerts_err:
            evidence_errors.append(alerts_err)

        evidence = DiagnosticsEvidence(
            device_status=device_status,
            telemetry_health=telemetry_health,
            disconnect_events=disconnect_events or [],
            active_alerts=active_alerts or [],
            graylog_status="ok" if device_status is not None else "partial",
            errors=evidence_errors,
        )

        ai = DiagnosticsAI(mentor)
        likely_issue_cause, recommended_steps, heuristic_summary, ai_summary = await ai.interpret(
            device_id=device_id,
            device_label=device_label,
            evidence=evidence,
        )

        return DeviceDiagnosticsResult(
            device_id=device_id,
            device_label=device_label,
            evidence=evidence,
            likely_issue_cause=likely_issue_cause,
            recommended_troubleshooting=recommended_steps,
            ai_summary=ai_summary,
        )
