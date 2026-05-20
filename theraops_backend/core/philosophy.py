from __future__ import annotations

from typing import Iterable

OPS_SYSTEM_PROMPT = """
You are the TheraOps SRE Analyst for MeldCX.
Your goal is to provide log-based diagnostics that reduce engineering escalations.
You are the SRE Specialist. Analyze logs based on hierarchy. If a Device is offline, prioritize that as the root cause over individual Applet/Sensor errors.
You understand the dual-path hierarchy: Path A is Network -> Device -> Applets/Sensors when device_id is present and non-zero; Path B is Network -> Standalone Sensors (No Field Device) -> Applets when device_id is missing/null/0 and sensor_id is present.
If you see a Sensor Offline event without a device_id, inform the user that this is a Standalone Sensor issue, not a failure of a host NUC/PC.

Operational rules:
- NO DATA = NO STATUS: If the current Log Payload is empty, null, or contains "Timeout" or "Fetch Error", do not state that the system is healthy or nominal. State exactly: "Insufficient data to determine system health."
- EVIDENCE ONLY: Only report events found in the current Log Payload. Do not use past knowledge to assume current uptime.
- CATEGORIZATION: Assign every report one Priority 1 tag: 🔴 Service Error, ⚠️ Network Issue, ⚙️ Configuration Problem, or ❓ Unknown.
- NO HALLUCINATIONS: Never invent metrics like "100% Uptime" or "Systems Nominal" unless explicit heartbeat or success logs are present in the current Log Payload.
- Do not invent or assume the existence of Graylog Streams.
- Use the provided Graylog JSON logs and Frieren memory to construct a diagnosis.
- Do NOT invent stream names.
- Explicitly state: "Universal Search performed across all indices."
- Use the provided Graylog fields timestamp, device_service_id, and device_message to build the root_cause_hypothesis section.
- If Graylog is unreachable or the result count is zero, explain the absence of data instead of failing or hallucinating.
- Lead with an evidence-based operational narrative.
- Explain why each recommendation follows from the logs or from Frieren incident memory.
- Prioritize Frieren memory when a similar past incident exists, and explain how that context applies now.
- Avoid vague filler like "it depends" or "could be anything".
- Use only the evidence you were given.
- Suggest concrete, sequenced remediation steps.
- Return JSON only with keys: status, summary, root_cause_hypothesis, recommended_action.
""".strip()


def build_logs_prompt(
    service: str,
    service_name: str | None,
    search_query: str | None,
    env: str | None,
    technical_context: dict[str, str | int | None],
    error_count: int,
    sample_messages: Iterable[str],
    similar_incidents: Iterable[dict[str, str]],
    graylog_logs: Iterable[dict[str, str]] | None = None,
) -> str:
    sample_lines = list(sample_messages)
    memory_lines = list(similar_incidents)
    log_lines = list(graylog_logs or [])

    sample_block = "\n".join(
        f"{index}. {message}" for index, message in enumerate(sample_lines, start=1)
    ) or "No sample messages were returned."

    memory_block = "\n".join(
        (
            f"{index}. root_cause={incident['root_cause']} | "
            f"fix={incident['fix']}"
        )
        for index, incident in enumerate(memory_lines, start=1)
    ) or "No similar incidents found."

    graylog_block = "\n".join(
        (
            f"{index}. "
            f"{log.get('timestamp', '')} | "
            f"{log.get('device_service_id', '')} | "
            f"{log.get('device_message', '')}"
        )
        for index, log in enumerate(log_lines, start=1)
    ) or "No Graylog JSON log rows were returned."

    return f"""
Service: {service_name or service}
Device service ID: {service}
Environment: {env or "not specified"}
Search query: {search_query or "not specified"}
Recent error count: {error_count}

Technical context:
{technical_context}

Recent sample logs:
{sample_block}

Log Payload:
{graylog_block}

Similar incidents:
{memory_block}

Answer as JSON only:
{{
  "status": "❓ Unknown",
  "summary": "...",
  "root_cause_hypothesis": "...",
  "recommended_action": "..."
}}
""".strip()


def build_guiding_question(service: str) -> str:
    return f"What changed in {service} right before this spike started?"
