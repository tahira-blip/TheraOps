from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from theraops_backend.core.philosophy import OPS_SYSTEM_PROMPT, build_logs_prompt

logger = logging.getLogger(__name__)


DIAGNOSIS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "summary": {"type": "string"},
        "root_cause_hypothesis": {"type": "string"},
        "recommended_action": {"type": "string"},
    },
    "required": [
        "status",
        "summary",
        "root_cause_hypothesis",
        "recommended_action",
    ],
}

CHAT_INTENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["FETCH_LOGS", "ANSWER"]},
        "window_seconds": {"type": "integer"},
        "message": {"type": "string"},
    },
    "required": ["action"],
}

OFFLINE_INVESTIGATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slack_report": {"type": "string"},
    },
    "required": ["slack_report"],
}

RELIABLE_SRE_PROMPT = OPS_SYSTEM_PROMPT


@dataclass
class LogDiagnosis:
    status: str
    summary: str
    root_cause_hypothesis: str
    recommended_action: str

    def to_slack_text(self) -> str:
        return "\n".join(
            [
                f"*Status*: {self.status}",
                f"*Summary*: {self.summary}",
                f"*Root Cause Hypothesis*: {self.root_cause_hypothesis}",
                f"*Recommended Action*: {self.recommended_action}",
            ]
        )


class FlammeMentor:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.custom_llm_url = settings.custom_llm_url.rstrip("/") if settings.custom_llm_url else ""
        self.custom_llm_model = (
            settings.llm_model
            or settings.ngrok_llm_model
            or getattr(settings, "custom_llm_model", "")
            or "google/gemma-2b"
        )
        self.gemini_api_key = settings.gemini_api_key
        self.gemini_model = settings.gemini_model
        self._client = httpx.AsyncClient(timeout=20.0)
        logger.info(
            "Flamme LLM: custom=%s/%s, gemini=%s",
            self.custom_llm_url,
            self.custom_llm_model,
            self.gemini_model,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def diagnose_logs(
        self,
        service: str,
        error_count: int,
        sample_messages: list[str],
        similar_incidents: Iterable[object],
        service_name: str | None = None,
        primary_stream: str | None = None,
        search_query: str | None = None,
        env: str | None = None,
        technical_context: dict[str, str | int | None] | None = None,
        graylog_logs: list[dict[str, str]] | None = None,
    ) -> str:
        similar_incident_list = list(similar_incidents)
        memory_payload = [
            {
                "service": getattr(incident, "service", ""),
                "root_cause": getattr(incident, "root_cause", ""),
                "fix": getattr(incident, "fix", ""),
            }
            for incident in similar_incident_list
        ]

        prompt = build_logs_prompt(
            service=service,
            service_name=service_name,
            search_query=search_query,
            env=env,
            technical_context=technical_context or {},
            error_count=error_count,
            sample_messages=sample_messages,
            similar_incidents=memory_payload,
            graylog_logs=graylog_logs,
        )

        logger.info("[FLAMME] Prompt length: %d chars, similar_incidents: %d", len(prompt), len(memory_payload))

        try:
            if self.custom_llm_url:
                logger.info("[FLAMME] Trying custom LLM: %s / %s", self.custom_llm_url, self.custom_llm_model)
                diagnosis = await self._generate_with_custom_llm(prompt)
                return diagnosis.to_slack_text()
        except Exception as exc:
            logger.warning("[FLAMME] Custom LLM failed: %s", exc)

        try:
            logger.info("[FLAMME] Trying Gemini: %s", self.gemini_model)
            diagnosis = await self._generate_with_gemini(prompt)
            return diagnosis.to_slack_text()
        except Exception as exc:
            logger.exception("[FLAMME] Gemini failed: %s", exc)

        logger.warning("[FLAMME] All LLMs failed, using heuristic")
        return self._heuristic_diagnosis(
            service=service,
            service_name=service_name,
            env=env,
            primary_stream=primary_stream,
            search_query=search_query,
            technical_context=technical_context,
            graylog_logs=graylog_logs,
            error_count=error_count,
            sample_messages=sample_messages,
            similar_incidents=memory_payload,
        ).to_slack_text()

    def format_spike_alert(
        self,
        service: str,
        percent_spike: float,
        error_count: int,
        sample_message: str,
        baseline: float = 0.0,
        window_seconds: int = 300,
        cooldown_minutes: int = 15,
    ) -> str:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        window_min = window_seconds // 60
        baseline_display = f"{baseline:.0f}" if baseline > 0 else "0"

        return "\n".join(
            [
                f":rotating_light: *Error spike in `{service}`* - {now_utc}",
                f"Spike: *{percent_spike:.1f}% above baseline* ({error_count} -> {baseline_display})",
                f"Time window: last {window_min} min",
                f"Sample: `{sample_message}`",
                f"Suggested action: `/thera logs {service}`",
                f"Cool down: {cooldown_minutes} min remaining",
            ]
        )

    async def format_offline_investigation(self, payload: dict[str, Any]) -> str:
        prompt = self._build_offline_prompt(payload)
        try:
            logger.info("[FLAMME] Formatting offline investigation with Gemini")
            response = await self._client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent",
                params={"key": self.gemini_api_key},
                json={
                    "systemInstruction": {
                        "parts": [
                            {
                                "text": (
                                    "You are Flamme, a Senior SRE Analyst formatting a TheraOps offline-device "
                                    "investigation for Slack. Use only the provided counts and device IDs. "
                                    "Use the provided device_service_name in the title. Include clear Slack headings "
                                    "for Graceful Disconnects and Critical Outages. Do not invent devices, streams, "
                                    "or causes. Return JSON only with key slack_report."
                                )
                            }
                        ],
                    },
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "responseSchema": OFFLINE_INVESTIGATION_RESPONSE_SCHEMA,
                        "temperature": 0.1,
                    },
                },
            )
            response.raise_for_status()
            parsed = json.loads(self._extract_text(response.json()))
            slack_report = str(parsed.get("slack_report", "")).strip()
            if slack_report:
                return slack_report
        except Exception as exc:
            logger.exception("[FLAMME] Offline investigation formatting failed: %s", exc)

        return self._heuristic_offline_report(payload)

    def _build_offline_prompt(self, payload: dict[str, Any]) -> str:
        return (
            "Format this hybrid offline detection result for Slack.\n"
            "Use these exact headings: Graceful Disconnects, Critical Outages, Healthy Heartbeats.\n"
            "Explain that Graceful Disconnects are stale devices with stream_stopped evidence, and "
            "Critical Outages are stale devices without stream_stopped evidence.\n\n"
            f"Investigation payload:\n{json.dumps(payload, indent=2)}"
        )

    def _heuristic_offline_report(self, payload: dict[str, Any]) -> str:
        service_name = str(payload.get("device_service_name") or payload.get("device_service_id") or "Unknown service")
        checked_at = str(payload.get("checked_at") or "unknown time")
        counts = payload.get("counts", {})
        graceful = payload.get("graceful_disconnects", [])
        critical = payload.get("critical_outages", [])
        healthy = payload.get("healthy_devices", [])

        def device_lines(items: list[dict[str, Any]]) -> str:
            if not items:
                return "None found."
            return "\n".join(
                f"- `{item.get('device_device_id', 'unknown')}` last seen `{item.get('last_seen') or 'unknown'}`"
                for item in items[:20]
            )

        return "\n".join(
            [
                f"*Offline device investigation: {service_name}*",
                f"Checked at: `{checked_at}`",
                (
                    f"Summary: {counts.get('stale_devices', 0)} stale device(s), "
                    f"{counts.get('graceful_disconnects', 0)} graceful disconnect(s), "
                    f"{counts.get('critical_outages', 0)} critical outage(s)."
                ),
                "",
                "*Graceful Disconnects*",
                device_lines(graceful),
                "",
                "*Critical Outages*",
                device_lines(critical),
                "",
                "*Healthy Heartbeats*",
                f"{counts.get('healthy_devices', len(healthy))} device(s) sent heartbeat within the last 5 minutes.",
            ]
        )

    async def _generate_with_custom_llm(self, prompt: str) -> LogDiagnosis:
        if not self.custom_llm_url:
            raise RuntimeError("CUSTOM_LLM_URL not set")
        
        json_instruction = (
            "\n\nCRITICAL: You MUST return ONLY valid JSON matching this exact schema. "
            f"Do not include markdown blocks or extra text:\n{json.dumps(DIAGNOSIS_RESPONSE_SCHEMA)}"
        )
        payload_primary = {
            "model": self.custom_llm_model,
            "system_prompt": RELIABLE_SRE_PROMPT,
            "input": prompt,
            "stream": False
        }
        try:
            logger.debug("[FLAMME] Custom LLM primary payload: %s", json.dumps(payload_primary)[:1000])
            response = await self._client.post(
                self.custom_llm_url,
                headers={"Content-Type": "application/json"},
                json=payload_primary,
                timeout=15.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            resp = exc.response
            if resp is not None and resp.status_code == 400:
                body = resp.text[:2000] if resp.text else "<no body>"
                logger.warning("[FLAMME] Custom LLM primary request returned 400; body: %s", body)
                logger.warning("[FLAMME] Custom LLM returned 400; retrying with chat-style payload")

                payload_chat = {
                    "model": self.custom_llm_model,
                    "messages": [
                        {"role": "system", "content": RELIABLE_SRE_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                }
                try:
                    logger.debug("[FLAMME] Custom LLM chat payload: %s", json.dumps({k: (v if k != 'messages' else '[truncated]' ) for k, v in payload_chat.items()})[:1000])
                    response = await self._client.post(
                        self.custom_llm_url,
                        headers={"Content-Type": "application/json"},
                        json=payload_chat,
                        timeout=15.0,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc2:
                    resp2 = exc2.response
                    body2 = resp2.text[:2000] if (resp2 and resp2.text) else "<no body>"
                    status2 = resp2.status_code if resp2 is not None else "<no status>"
                    logger.warning("[FLAMME] Custom LLM chat retry failed: %s body: %s", status2, body2)
                    raise
            else:
                # Unexpected non-400 status or no response body — re-raise to be handled upstream
                status = resp.status_code if resp is not None else "<no status>"
                body = resp.text[:2000] if (resp and resp.text) else "<no body>"
                logger.warning("[FLAMME] Custom LLM request failed: %s body: %s", status, body)
                raise

        payload = response.json()
        try:
            text = self._extract_custom_text(payload)
        except Exception:
            # If extraction fails, log the entire payload for diagnosis (truncated)
            logger.warning("[FLAMME] Custom LLM returned unexpected payload: %s", json.dumps(payload)[:2000])
            raise
        text = text.strip()
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx !=  -1:
            text = text[start_idx:end_idx+1]
        return self._diagnosis_from_dict(json.loads(text))

    async def _generate_with_gemini(self, prompt: str) -> LogDiagnosis:
        if not self.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

        response = await self._client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent",
            params={"key": self.gemini_api_key},
            json={
                "systemInstruction": {
                    "parts": [{"text": RELIABLE_SRE_PROMPT}],
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": DIAGNOSIS_RESPONSE_SCHEMA,
                    "temperature": 0.2,
                },
            },
        )
        response.raise_for_status()

        payload = response.json()
        text = self._extract_text(payload)
        return self._diagnosis_from_dict(json.loads(text))

    def _diagnosis_from_dict(self, data: dict[str, Any]) -> LogDiagnosis:
        return LogDiagnosis(
            status=str(data.get("status", "❓ Unknown")).strip() or "❓ Unknown",
            summary=str(data.get("summary", "")).strip(),
            root_cause_hypothesis=str(data.get("root_cause_hypothesis", "")).strip(),
            recommended_action=str(data.get("recommended_action", "")).strip(),
        )

    def _classify_log_status(
        self,
        *,
        graylog_status: str,
        error_count: int,
        sample_messages: list[str],
        graylog_logs: list[dict[str, str]],
    ) -> str:
        evidence = " ".join(
            [
                graylog_status,
                *sample_messages,
                *[
                    str(log.get("device_message", ""))
                    for log in graylog_logs
                ],
            ]
        ).lower()

        if graylog_status != "ok" or "timeout" in evidence or "fetch error" in evidence:
            return "⚠️ Network Issue"
        if error_count == 0:
            return "❓ Unknown"
        if any(term in evidence for term in ["config", "configuration", "missing", "invalid", "unauthorized", "forbidden", "permission", "credential", "secret", "env var"]):
            return "⚙️ Configuration Problem"
        if any(term in evidence for term in ["network", "dns", "connection refused", "connection reset", "unreachable", "timeout", "timed out"]):
            return "⚠️ Network Issue"
        if any(term in evidence for term in ["error", "exception", "failed", "failure", "500", "503", "crash"]):
            return "🔴 Service Error"
        return "❓ Unknown"

    def _extract_custom_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output"), list):
            message = next((item for item in payload["output"] if item.get("type") == "message"), None)
            if message:
                return str(message.get("content", ""))
        if "response" in payload:
            return str(payload["response"])
        if "text" in payload:
            return str(payload["text"])
        if payload.get("choices"):
            return str(payload["choices"][0]["message"]["content"])
        raise ValueError("Custom LLM returned empty response")

    def _extract_text(self, payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            raise ValueError("Gemini returned no candidates.")

        content = candidates[0].get("content", {})
        parts = content.get("parts") or []
        if not parts:
            raise ValueError("Gemini returned no content parts.")

        text = parts[0].get("text", "")
        if not text:
            raise ValueError("Gemini returned an empty text payload.")
        return str(text)

    def _heuristic_diagnosis(
        self,
        service: str,
        error_count: int,
        sample_messages: list[str],
        similar_incidents: list[dict[str, str]] | None = None,
        service_name: str | None = None,
        env: str | None = None,
        primary_stream: str | None = None,
        search_query: str | None = None,
        technical_context: dict[str, str | int | None] | None = None,
        graylog_logs: list[dict[str, str]] | None = None,
    ) -> LogDiagnosis:
        display_service = service_name or service
        # env_context = f" in `{env}`" if env else ""
        # search_context = f" matching `{search_query}`" if search_query else ""
        strongest_clue = sample_messages[0] if sample_messages else "No sample message returned by Graylog."
        incidents = similar_incidents or []
        context = technical_context or {}
        search_type = context.get("search_type", "Universal (All Streams)")
        lucene_query = context.get("query", f'device_service_id:"{service}"')
        graylog_status = str(context.get("graylog_status", "ok"))
        log_rows = graylog_logs or []

        if graylog_status != "ok":
            return LogDiagnosis(
                status=self._classify_log_status(
                    graylog_status=graylog_status,
                    error_count=error_count,
                    sample_messages=sample_messages,
                    graylog_logs=log_rows,
                ),
                summary="Insufficient data to determine system health.",
                root_cause_hypothesis=(
                    "Universal Search performed across all indices. "
                    f"The search `{lucene_query}` did not return usable log rows because Graylog status was `{graylog_status}`. "
                    "I am not inventing stream names or substituting unverified evidence."
                ),
                recommended_action=(
                    "First restore or verify Graylog API connectivity and rerun the same query/window. "
                    "In parallel, check live service health and recent deploys only as supporting signals, not as replacements for log evidence."
                ),
            )

        if error_count == 0:
            return LogDiagnosis(
                status="❓ Unknown",
                summary="Insufficient data to determine system health.",
                root_cause_hypothesis=(
                    "Universal Search performed across all indices. "
                    f"The {search_type} query `{lucene_query}` returned 0 results, so there are no timestamp, "
                    "device_service_id, or device_message fields to cite for this window."
                ),
                recommended_action=(
                    "First validate the incident time window and confirm the service is emitting `device_service_id` and `env` tags. "
                    "If the user-reported failure happened earlier, rerun with a wider window before escalating."
                ),
            )

        log_evidence = "; ".join(
            (
                f"{log.get('timestamp', '')} "
                f"{log.get('device_service_id', service)} "
                f"{log.get('device_message', '')}"
            ).strip()
            for log in log_rows[:3]
        ) or strongest_clue

        if incidents:
            top_memory = incidents[0]
            memory_strategy = (
                f"Frieren found a similar incident for `{top_memory.get('service', service)}`: "
                f"{top_memory.get('root_cause', 'root cause not recorded')}. "
                f"The prior fix was: {top_memory.get('fix', 'fix not recorded')}. "
                "Apply that context first by checking whether the same dependency, config, or deploy path is present in this spike."
            )
        else:
            memory_strategy = (
                "Frieren did not find a matching resolved incident, so treat this as a fresh investigation. "
                "Start with the repeated log signature and correlate it against the latest deploy, config, and dependency changes."
            )

        return LogDiagnosis(
            status=self._classify_log_status(
                graylog_status=graylog_status,
                error_count=error_count,
                sample_messages=sample_messages,
                graylog_logs=log_rows,
            ),
            summary=(
                f"`{display_service}` has {error_count} matching Graylog event(s){env_context}{search_context}. "
                f"The strongest current signal is `{strongest_clue}`."
            ),
            root_cause_hypothesis=(
                "Universal Search performed across all indices. "
                f"The {search_type} ran `{lucene_query}` and returned {error_count} result(s). "
                f"Graylog fields reviewed: {log_evidence}. "
                "Use the full stack trace and adjacent events to separate one repeated failure mode from unrelated noise."
            ),
            recommended_action=(
                f"{memory_strategy} Then pull the surrounding logs for the sample event, identify the first occurrence, "
                f"and compare it with recent changes to `{display_service}`."
            ),
        )
    async def analyze_chat_intent(self, thread_messages: list[dict[str, str]]) -> dict[str, Any]:
        latest_text = thread_messages[-1]["text"] if thread_messages else ""
        lowered = latest_text.lower()
        if "earlier interval" in lowered or "further back" in lowered or "look further back" in lowered:
            return {"action": "FETCH_LOGS", "window_seconds": 3600}

        if not self.gemini_api_key:
            return {"action": "ANSWER", "message": "No log refetch requested."}

        prompt = (
            "Analyze the latest Slack reply in this incident thread. "
            "If the user asks to query an earlier interval, look further back, widen the window, "
            "or rerun logs for more history, return action FETCH_LOGS and window_seconds 3600. "
            "Otherwise return action ANSWER.\n\n"
            f"Thread messages: {thread_messages}"
        )
        response = await self._client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent",
            params={"key": self.gemini_api_key},
            json={
                "systemInstruction": {
                    "parts": [{"text": "Return only structured JSON for conversational incident intent."}],
                },
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": CHAT_INTENT_RESPONSE_SCHEMA,
                    "temperature": 0.0,
                },
            },
        )
        response.raise_for_status()
        return json.loads(self._extract_text(response.json()))
