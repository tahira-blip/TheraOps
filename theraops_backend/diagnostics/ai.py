from __future__ import annotations

import logging
from typing import Any

from theraops_backend.diagnostics.models import DeviceDiagnosticsResult, DiagnosticsEvidence

logger = logging.getLogger(__name__)


def _format_evidence_for_prompt(device_id: str, device_label: str | None, evidence: DiagnosticsEvidence) -> str:
    # Keep prompt compact; only include what we have.
    def safe(v: Any) -> Any:
        return v if v is not None else None

    device_status = evidence.device_status.__dict__ if evidence.device_status else None
    telemetry_health = evidence.telemetry_health.__dict__ if evidence.telemetry_health else None

    disconnects = [
        {
            "kind": ev.kind,
            "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
            "device_id": ev.device_id,
        }
        for ev in evidence.disconnect_events[:10]
    ]

    alerts = [
        {
            "severity": a.severity,
            "title": a.title,
            "detail": a.detail,
        }
        for a in evidence.active_alerts[:10]
    ]

    return (
        f"Device: {device_label or device_id}\n"
        f"Status: {device_status}\n"
        f"Telemetry health: {telemetry_health}\n"
        f"Recent disconnect/reconnect events: {disconnects}\n"
        f"Active alerts: {alerts}\n"
        f"Graylog status: {evidence.graylog_status}\n"
        f"Provider errors: {evidence.errors}"
    )


class DiagnosticsAI:
    """
    Uses FlammeMentor when possible, but can fall back to deterministic heuristics.
    """

    def __init__(self, mentor: Any) -> None:
        self.mentor = mentor

    async def interpret(
        self,
        *,
        device_id: str,
        device_label: str | None,
        evidence: DiagnosticsEvidence,
    ) -> tuple[str, str, list[str], str | None]:
        # 1) Best: heuristic to produce likely cause + recommended steps
        likely_issue_cause, recommended_steps, heuristic_summary = self._heuristic_interpret(
            device_id=device_id,
            device_label=device_label,
            evidence=evidence,
        )

        # Keep diagnostics deterministic. The generic log mentor expects error
        # rows and can misstate empty device evidence as matching log events.
        # “Receptionist technical chat” pathway by calling analyze_chat_intent is not suitable.
        return likely_issue_cause, recommended_steps, heuristic_summary, None

    def _heuristic_interpret(
        self,
        *,
        device_id: str,
        device_label: str | None,
        evidence: DiagnosticsEvidence,
    ) -> tuple[str, list[str], str]:
        status = evidence.device_status.status if evidence.device_status else "unknown"
        telemetry = evidence.telemetry_health
        disconnects = evidence.disconnect_events
        alerts = evidence.active_alerts

        device_name = device_label or device_id

        if status == "offline":
            likely_issue_cause = "Device is not reporting heartbeats (possible power/network outage or device crash)."
            recommended_steps = [
                "Verify device power/battery state and any upstream power feed.",
                "Check network connectivity at the nearest hop (switch/router) and confirm link flaps.",
                "If available, inspect the latest stream_stopped events for the most recent failure mode.",
                "Reboot the device and confirm heartbeat resumes within 5 minutes.",
            ]
            summary = f"*Device {device_name}* appears *Offline* (no recent heartbeat)."
            return likely_issue_cause, recommended_steps, summary

        if status == "degraded":
            likely_issue_cause = "Recent Graylog events indicate a component-level issue, but heartbeat telemetry is missing or incomplete."
            recommended_steps = [
                "Review the related active alert event_code and identity_name for the failing component.",
                "Confirm whether heartbeat telemetry is expected for this device or if it only reports non-session events.",
                "Check the sensor/module path associated with the event and validate physical/network connectivity.",
                "If the event repeats, compare the first occurrence with recent deploys, config changes, or site/network changes.",
            ]
            # extra clue from stream_stopped
            if disconnects:
                kind_counts = {}
                for ev in disconnects[:10]:
                    kind_counts[ev.kind] = kind_counts.get(ev.kind, 0) + 1
                if "disconnect" in kind_counts:
                    likely_issue_cause = (
                        "Recent disconnect/stream stopping indicates unstable link or device process restarts."
                    )
            if telemetry and telemetry.sensor_health:
                likely_issue_cause = (
                    f"Telemetry indicates sensor/telemetry health issues ({telemetry.sensor_health}); potential sensor communication instability."
                )
                recommended_steps.insert(0, "Validate sensor/module health and recent telemetry freshness (look for stale or missing fields).")
            if alerts:
                alert_titles = ", ".join(alert.title for alert in alerts[:3])
                likely_issue_cause = f"Recent active Graylog event(s): {alert_titles}."
            summary = f"*Device {device_name}* appears *Degraded* (intermittent or unstable connectivity)."
            return likely_issue_cause, recommended_steps, summary

        if status == "online":
            # Online
            likely_issue_cause = "Device is currently healthy; if users see a symptom, it may be transient, workload-dependent, or localized to a specific component/sensor."
            recommended_steps = [
                "Confirm the symptom timeline (start time) and compare it with telemetry freshness.",
                "Check active alerts and recent disconnect/reconnect events for correlation.",
                "If issues persist, run targeted log/telemetry queries for the suspected component/sensor.",
            ]
            summary = f"*Device {device_name}* appears *Online* (heartbeats present)."
            return likely_issue_cause, recommended_steps, summary

        # fallback when status is unexpected/unknown
        return (
            "Insufficient evidence to determine likely issue cause.",
            [
                "Confirm device identity (device_id / serial).",
                "Check Graylog connectivity for heartbeat and stream events.",
                "If telemetry fields are incomplete, increase query window and retry.",
                "Escalate with raw Graylog evidence attached to the Slack thread for faster triage.",
            ],
            f"*Device {device_name}* has *Unknown* status.",
        )
