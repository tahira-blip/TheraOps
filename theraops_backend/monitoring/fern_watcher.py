from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from theraops_backend.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ErrorSummary:
    service: str
    error_count: int
    sample_messages: list[str]
    status_code: int
    sample_logs: list[dict[str, str]] | None = None


@dataclass
# a class meant to represent the result of a Graylog search, which may be used for more than just error summaries in the future
class GraylogSearchResult: 
    query: str
    stream: str | None
    total_results: int
    messages: list[dict[str, Any]]
    status_code: int


@dataclass
class StreamStoppedEvent:
    device_device_id: str
    timestamp: datetime | None
    raw: dict[str, Any]


@dataclass
class DeviceHeartbeat:
    device_device_id: str
    last_seen: datetime | None
    raw: dict[str, Any]


@dataclass
class DeviceEvent:
    device_id: str
    timestamp: datetime | None
    event_code: str | None
    event_name: str | None
    stream: str | None
    identity_name: str | None
    network_id: str | None
    raw: dict[str, Any]


@dataclass
class AlertDecision:
    service: str
    baseline: float
    current_count: int
    should_alert: bool
    reason: str
    is_warmup: bool
    sample_messages: list[str]
    status_code: int


class GraylogClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_base_url = self._normalize_api_base_url(self.base_url)
        self.token = token
        # Increase timeout to reduce transient read errors and enable retries
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_error_summary(
        self,
        service: str,
        window_seconds: int = 300,
        limit: int = 5,
        search_query: str | None = None,
        env: str | None = None,
        primary_stream: str | None = None,
    ) -> ErrorSummary:
        if not self.base_url or not self.token:
            raise RuntimeError("GRAYLOG_URL and GRAYLOG_TOKEN must both be set.")

        query = self._build_error_query(
            device_service_id=service,
            search_query=search_query,
            env=env,
        )
        # Best-effort retry for transient read/network errors
        response = None
        last_exc = None
        for attempt in range(2):
            try:
                response = await self._client.get(
                    f"{self.api_base_url}/search/universal/relative",
                    params={
                        "query": query,
                        "range": window_seconds,
                        "limit": limit,
                        "fields": "device_message,timestamp,device_service_id,device_level,environment,env,device_environment,stage",
                    },
                    auth=(self.token, "token"),
                    headers={
                        "Accept": "application/json",
                        "X-Requested-By": "theraops-backend",
                    },
                )
                response.raise_for_status()
                last_exc = None
                break
            except (httpx.ReadError, httpx.TransportError) as exc:
                last_exc = exc
                logger.warning("Graylog search_relative attempt %d failed: %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                raise

        payload = self._parse_json_response(response, query=query)
        sample_messages: list[str] = []
        sample_logs: list[dict[str, str]] = []
        for item in payload.get("messages") or []:
            if not isinstance(item, dict):
                continue
            message = item.get("message", {})
            if not isinstance(message, dict):
                continue
            raw_message = message.get("device_message", "")
            if not raw_message:  # Fallback just in case
                raw_message = message.get("message", "")
            cleaned_message = self._clean_message(raw_message)
            if cleaned_message:
                sample_messages.append(cleaned_message)
            sample_logs.append(
                {
                    "timestamp": str(message.get("timestamp", "")),
                    "device_service_id": str(message.get("device_service_id", service)),
                    "device_message": cleaned_message or str(raw_message),
                }
            )

        return ErrorSummary(
            service=service,
            error_count=int(payload.get("total_results", 0)),
            sample_messages=sample_messages[:limit],
            status_code=response.status_code,
            sample_logs=sample_logs[:limit],
        )

    async def search_relative(
        self,
        *,
        query: str,
        window_seconds: int,
        fields: list[str],
        limit: int = 500,
        stream: str | None = None,
    ) -> GraylogSearchResult:
        if not self.base_url or not self.token:
            raise RuntimeError("GRAYLOG_URL and GRAYLOG_TOKEN must both be set.")

        params: dict[str, Any] = {
            "query": query,
            "range": window_seconds,
            "limit": limit,
            "fields": ",".join(fields),
        }
        if stream:
            params["filter"] = f"streams:{stream}"

        # Best-effort retry for transient read/network errors
        response = None
        last_exc = None
        for attempt in range(2):
            try:
                response = await self._client.get(
                    f"{self.api_base_url}/search/universal/relative",
                    params=params,
                    auth=(self.token, "token"),
                    headers={
                        "Accept": "application/json",
                        "X-Requested-By": "theraops-backend",
                    },
                )
                response.raise_for_status()
                last_exc = None
                break
            except (httpx.ReadError, httpx.TransportError) as exc:
                last_exc = exc
                logger.warning("Graylog search_relative attempt %d failed: %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                raise

        payload = self._parse_json_response(response, query=query, stream=stream)
        messages = [
            item.get("message", {})
            for item in payload.get("messages") or []
            if isinstance(item, dict) and isinstance(item.get("message", {}), dict)
        ]

        return GraylogSearchResult(
            query=query,
            stream=stream,
            total_results=int(payload.get("total_results", 0)),
            messages=messages,
            status_code=response.status_code,
        )

    def _normalize_api_base_url(self, base_url: str) -> str:
        if not base_url:
            return ""

        parts = urlsplit(base_url.rstrip("/"))
        path = parts.path.rstrip("/")
        if path.endswith("/api") or path == "/api":
            return urlunsplit(parts)

        api_path = f"{path}/api" if path else "/api"
        return urlunsplit((parts.scheme, parts.netloc, api_path, parts.query, parts.fragment))

    def _parse_json_response(
        self,
        response: httpx.Response,
        *,
        query: str,
        stream: str | None = None,
    ) -> dict[str, Any]:
        if not response.content or not response.content.strip():
            logger.warning(
                "Graylog returned an empty %s response for query=%r stream=%r; treating it as no results.",
                response.status_code,
                query,
                stream,
            )
            return {"total_results": 0, "messages": []}

        try:
            payload = response.json()
        except ValueError as exc:
            content_type = response.headers.get("content-type", "")
            preview = response.text[:300].replace("\n", "\\n")
            raise RuntimeError(
                "Graylog returned non-JSON response "
                f"(status={response.status_code}, content_type={content_type!r}, "
                f"query={query!r}, stream={stream!r}, body_preview={preview!r})"
            ) from exc

        if not isinstance(payload, dict):
            raise RuntimeError(
                "Graylog returned unexpected JSON payload "
                f"(status={response.status_code}, query={query!r}, stream={stream!r}, "
                f"type={type(payload).__name__})"
            )

        return payload

    async def fetch_stream_stopped_events(
        self,
        *,
        service: str,
        window_seconds: int = 3600,
        stream: str = "production-device-error-events",
        limit: int = 500,
    ) -> list[StreamStoppedEvent]:
        query = f"({self._diagnostic_identifier_query(service)}) AND device_event_type:\"stream_stopped\""
        result = await self.search_relative(
            query=query,
            window_seconds=window_seconds,
            stream=stream,
            limit=limit,
            fields=[
                "timestamp",
                "device_service_id",
                "device_service_name",
                "device_id",
                "device_device_id",
                "device_event_type",
                "device_message",
            ],
        )

        events: list[StreamStoppedEvent] = []
        for message in result.messages:
            device_id = str(message.get("device_device_id") or message.get("device_id") or "").strip()
            if not device_id:
                continue
            events.append(
                StreamStoppedEvent(
                    device_device_id=device_id,
                    timestamp=self._parse_graylog_timestamp(message.get("timestamp")),
                    raw=message,
                )
            )
        return events

    async def get_network_issues(self, timerange_hours: int = 24) -> list[dict]:
        # Network-first operational query: target production-non-session-events stream
        # and only look for the critical offline/connection event codes.
        query = 'event_code:("con_0002" OR "pc_0010" OR "pc_0008" OR "scon_0002" OR "samdt_0006" OR "pc_0012" OR "samdt_0003")'
        try:
            result = await self.search_relative(
                query=query,
                window_seconds=timerange_hours * 3600,
                fields=[
                    "timestamp",
                    "event_code",
                    "device_id",
                    "device_name",
                    "network_code",
                    "network_id",
                    "location_name",
                ],
                limit=2000,
                stream="production-non-session-events",
            )
        except Exception as exc:  # pragma: no cover - best-effort network/graylog call
            logger.error("Graylog get_network_issues failed: %s", exc)
            return []

        events: list[dict] = []
        for message in result.messages:
            events.append(
                {
                    "timestamp": message.get("timestamp"),
                    "event_code": message.get("event_code") or message.get("event") or "",
                    "device_id": message.get("device_id") or message.get("device_device_id") or None,
                    "device_name": message.get("device_name") or message.get("device_service_name") or None,
                    "network_code": message.get("network_code"),
                    "network_id": int(message.get("network_id")) if message.get("network_id") else None,
                    "location_name": message.get("location_name"),
                    # provide human-readable short label for the event_code to make
                    # downstream grouping simpler for consumers
                    "label": None,
                }
            )

        # attach label mapping where possible (best-effort)
        from theraops_backend.backend.categorizer import ISSUE_CATEGORIES

        for ev in events:
            ec = (ev.get("event_code") or "").strip()
            label = None
            for meta in ISSUE_CATEGORIES.values():
                if ec in meta.get("event_codes", []):
                    label = meta.get("label")
                    break
            ev["label"] = label

        return events

    async def fetch_latest_heartbeats(
        self,
        *,
        service: str,
        window_seconds: int = 3600,
        stream: str = "production-heartbeat-fleet",
        limit: int = 1000,
    ) -> list[DeviceHeartbeat]:
        fields = [
            "timestamp",
            "device_service_id",
            "device_service_name",
            "device_id",
            "device_device_id",
            "device_serial_id",
            "device_device_serial_id",
            "device_event_type",
            "device_message",
        ]
        streams = [stream]
        if stream == "production-heartbeat-fleet":
            streams.append("production-heartbeat-events")
        messages: list[dict[str, Any]] = []
        query = f"({self._diagnostic_identifier_query(service)})"
        for heartbeat_stream in streams:
            result = await self.search_relative(
                query=query,
                window_seconds=window_seconds,
                stream=heartbeat_stream,
                limit=limit,
                fields=fields,
            )
            messages.extend(result.messages)

        latest_by_device: dict[str, DeviceHeartbeat] = {}
        for message in messages:
            device_id = str(message.get("device_device_id") or message.get("device_id") or "").strip()
            if not device_id:
                continue
            heartbeat = DeviceHeartbeat(
                device_device_id=device_id,
                last_seen=self._parse_graylog_timestamp(message.get("timestamp")),
                raw=message,
            )
            existing = latest_by_device.get(device_id)
            if not existing or self._is_later(heartbeat.last_seen, existing.last_seen):
                latest_by_device[device_id] = heartbeat

        return sorted(latest_by_device.values(), key=lambda item: item.device_device_id)

    async def fetch_device_events(
        self,
        *,
        device_id: str,
        window_seconds: int = 24 * 3600,
        limit: int = 200,
        streams: list[str | None] | None = None,
    ) -> list[DeviceEvent]:
        raw_device_id = str(device_id).strip()
        normalized_ids = self._diagnostic_identifier_values(raw_device_id)

        field_prefix = None
        raw_field_value = raw_device_id
        if ":" in raw_device_id:
            maybe_field, maybe_value = raw_device_id.split(":", 1)
            if maybe_field.strip() in {
                "device_id",
                "device_device_id",
                "device_service_id",
                "device_serial_id",
                "device_device_serial_id",
                "serial",
            }:
                field_prefix = maybe_field.strip()
                raw_field_value = maybe_value.strip().strip('"')
        elif " " in raw_device_id:
            maybe_field, maybe_value = raw_device_id.split(None, 1)
            if maybe_field.strip() in {
                "device_id",
                "device_device_id",
                "device_service_id",
                "device_serial_id",
                "device_device_serial_id",
                "serial",
            }:
                field_prefix = maybe_field.strip()
                raw_field_value = maybe_value.strip().strip('"')

        candidates: list[str] = []
        if field_prefix:
            safe_value = self._escape_query_value(raw_field_value)
            candidates.append(f'{field_prefix}:"{safe_value}"')
            if raw_field_value.isdigit():
                candidates.append(f"{field_prefix}:{safe_value}")

        for value in normalized_ids:
            safe_value = self._escape_query_value(value)
            for field in (
                "device_id",
                "device_device_id",
                "device_serial_id",
                "device_device_serial_id",
                "serial",
            ):
                candidates.append(f'{field}:"{safe_value}"')
            if value.isdigit():
                candidates.append(f"device_id:{safe_value}")
                candidates.append(f"device_device_id:{safe_value}")
                candidates.append(f'device_service_id:"device-{safe_value}"')
            candidates.append(f'device_service_id:"{safe_value}"')

        seen_candidates: set[str] = set()
        candidates = [
            candidate
            for candidate in candidates
            if not (candidate in seen_candidates or seen_candidates.add(candidate))
        ]

        fields = [
            "timestamp",
            "event_code",
            "event_name",
            "device_id",
            "device_device_id",
            "device_service_id",
            "device_serial_id",
            "device_device_serial_id",
            "device_name",
            "device_service_name",
            "identity_name",
            "network_id",
            "network_code",
            "message",
            "device_message",
            "device_event_type",
            "app_code",
            "eku_ts",
            "sensor_id",
            "sensor_status",
            "path",
        ]

        if streams is None:
            streams = [
                "production-non-session-events",
                "production-heartbeat-events",
                "production-heartbeat-fleet",
                "production-device-error-events",
                None,
            ]

        events: list[DeviceEvent] = []
        seen_messages: set[tuple[str | None, str, str | None, str | None]] = set()
        query = "(" + " OR ".join(candidates) + ")"
        for candidate_stream in streams:
            result = await self.search_relative(
                query=query,
                window_seconds=window_seconds,
                fields=fields,
                limit=limit,
                stream=candidate_stream,
            )
            for message in result.messages:
                message = {**message, "_stream": candidate_stream}
                raw_device_id = message.get("device_id") or message.get("device_device_id") or device_id
                timestamp = self._parse_graylog_timestamp(message.get("timestamp"))
                event_code = (
                    str(message.get("event_code"))
                    if message.get("event_code")
                    else str(message.get("device_event_type"))
                    if message.get("device_event_type")
                    else None
                )
                event_name = (
                    str(message.get("event_name"))
                    if message.get("event_name")
                    else "Heartbeat"
                    if candidate_stream in {"production-heartbeat-events", "production-heartbeat-fleet"}
                    else None
                )
                dedupe_key = (
                    candidate_stream,
                    str(raw_device_id),
                    timestamp.isoformat() if timestamp else None,
                    event_code,
                )
                if dedupe_key in seen_messages:
                    continue
                seen_messages.add(dedupe_key)
                events.append(
                    DeviceEvent(
                        device_id=str(raw_device_id),
                        timestamp=timestamp,
                        event_code=event_code,
                        event_name=event_name,
                        stream=candidate_stream,
                        identity_name=str(message.get("identity_name")) if message.get("identity_name") else None,
                        network_id=str(message.get("network_id")) if message.get("network_id") not in (None, "") else None,
                        raw=message,
                    )
                )

        return sorted(events, key=lambda item: item.timestamp or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    def _diagnostic_identifier_values(self, value: str) -> list[str]:
        values = [value]
        lowered = value.lower()
        if ":" in value:
            _, field_value = value.split(":", 1)
            values.append(field_value.strip().strip('"'))
        elif " " in value:
            maybe_field, field_value = value.split(None, 1)
            if maybe_field.strip() in {
                "device_id",
                "device_device_id",
                "device_service_id",
                "device_serial_id",
                "device_device_serial_id",
                "serial",
            }:
                values.append(field_value.strip().strip('"'))
        if lowered.startswith("device "):
            values.append(value.split(None, 1)[1].strip())
        if lowered.startswith("device-"):
            values.append(value.split("-", 1)[1].strip())

        seen_values: set[str] = set()
        return [
            item
            for item in values
            if item and not (item in seen_values or seen_values.add(item))
        ]

    def _diagnostic_identifier_query(self, value: str) -> str:
        candidates: list[str] = []
        for item in self._diagnostic_identifier_values(str(value).strip()):
            safe_item = self._escape_query_value(item)
            candidates.extend(
                [
                    f'device_service_id:"{safe_item}"',
                    f'device_device_service_id:"{safe_item}"',
                    f'device_id:"{safe_item}"',
                    f'device_device_id:"{safe_item}"',
                    f'device_serial_id:"{safe_item}"',
                    f'device_device_serial_id:"{safe_item}"',
                ]
            )
            if item.isdigit():
                candidates.extend(
                    [
                        f"device_id:{safe_item}",
                        f"device_device_id:{safe_item}",
                        f'device_service_id:"device-{safe_item}"',
                    ]
                )

        seen_candidates: set[str] = set()
        unique_candidates = [
            candidate
            for candidate in candidates
            if not (candidate in seen_candidates or seen_candidates.add(candidate))
        ]
        return " OR ".join(unique_candidates) if unique_candidates else '*'

    def _escape_query_value(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def build_lucene_query(
        self,
        device_service_id: str,
        search_query: str | None = None,
        env: str | None = None,
    ) -> str:
        return self._build_error_query(
            device_service_id=device_service_id,
            search_query=search_query,
            env=env,
        )

    def _build_error_query(
        self,
        device_service_id: str,
        search_query: str | None = None,
        env: str | None = None,
    ) -> str:
        safe_service = self._escape_query_value(device_service_id)
        query_parts = [f'device_service_id:"{safe_service}"']

        if env:
            safe_env = self._escape_query_value(env)
            query_parts.append(f'env:"{safe_env}"')

        if search_query:
            safe_search_query = self._escape_query_value(search_query)
            query_parts.append(f'"{safe_search_query}"')

        return " AND ".join(query_parts)

    def _clean_message(self, message: str) -> str:
        collapsed = " ".join(message.split())
        return collapsed[:240]

    def _parse_graylog_timestamp(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    def _is_later(self, candidate: datetime | None, current: datetime | None) -> bool:
        if candidate is None:
            return False
        if current is None:
            return True
        return candidate > current


class SlackNotifier:
    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def post_message(self, channel_id: str, text: str) -> None:
        if not self.bot_token:
            raise RuntimeError("SLACK_BOT_TOKEN must be set for alerts.")

        response = await self._client.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {self.bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={"channel": channel_id, "text": text},
        )
        response.raise_for_status()

        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Slack API error: {payload.get('error', 'unknown_error')}")


class FernWatcher:
    def __init__(
        self,
        settings: Settings,
        graylog_client: GraylogClient,
        slack_notifier: SlackNotifier,
        mentor: object,
    ) -> None:
        self.settings = settings
        self.graylog_client = graylog_client
        self.slack_notifier = slack_notifier
        self.mentor = mentor
        self._stop_event = asyncio.Event()
        self._history = {
            service: deque(maxlen=settings.baseline_window_count)
            for service in settings.watch_services
        }
        self._last_alert_at: dict[str, datetime] = {}

    def stop(self) -> None:
        self._stop_event.set()

    async def run_forever(self) -> None:
        if not self.settings.watcher_enabled:
            logger.info("Fern watcher disabled. Missing Graylog, Slack, or watch service config.")
            return

        logger.info("Fern watcher started for services: %s", ", ".join(self.settings.watch_services))

        while not self._stop_event.is_set():
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Fern watcher poll failed.")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def poll_once(self) -> None:
        decisions = await self.collect_decisions()

        for service, result in zip(self.settings.watch_services, decisions):
            if isinstance(result, Exception):
                logger.error(
                    "Watcher poll failed for service %s",
                    service,
                    exc_info=(type(result), result, result.__traceback__),
                )
                continue

            await self._handle_alert_decision(result)

    async def collect_decisions(self) -> list[AlertDecision | Exception]:
        tasks = [self.evaluate_service(service) for service in self.settings.watch_services]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def evaluate_service(self, service: str) -> AlertDecision:
        summary = await self.graylog_client.fetch_error_summary(
            service=service,
            window_seconds=self.settings.error_window_seconds,
            limit=1,
        )
        history = self._history[service]
        baseline = self._baseline(history)
        should_alert, reason = self._alert_decision(service, summary.error_count, baseline)

        return AlertDecision(
            service=service,
            baseline=baseline,
            current_count=summary.error_count,
            should_alert=should_alert,
            reason=reason,
            is_warmup=len(history) < self._required_history_points(),
            sample_messages=summary.sample_messages,
            status_code=summary.status_code,
        )

    async def _handle_alert_decision(self, decision: AlertDecision) -> None:
        alerted = False

        if decision.should_alert:
            sample_message = (
                decision.sample_messages[0]
                if decision.sample_messages
                else "No sample message returned by Graylog."
            )
            percent_spike = self._percent_spike(decision.current_count, decision.baseline)
            alert_text = self.mentor.format_spike_alert(
                service=decision.service,
                percent_spike=percent_spike,
                error_count=decision.current_count,
                sample_message=sample_message,
                baseline=decision.baseline,
                window_seconds=self.settings.error_window_seconds,
                cooldown_minutes=self.settings.alert_cooldown_minutes,
            )
            await self.slack_notifier.post_message(
                self.settings.alert_channel_id,
                alert_text,
            )
            alerted = True

        self.record_decision(decision, alerted=alerted)

    def record_decision(self, decision: AlertDecision, alerted: bool) -> None:
        self._history[decision.service].append(decision.current_count)
        if alerted:
            self._last_alert_at[decision.service] = datetime.now(timezone.utc)

    def _baseline(self, history: deque[int]) -> float:
        if not history:
            return 0.0
        return sum(history) / len(history)

    def _required_history_points(self) -> int:
        return min(3, self.settings.baseline_window_count)

    def _alert_decision(self, service: str, current_count: int, baseline: float) -> tuple[bool, str]:
        history = self._history[service]
        required_points = self._required_history_points()
        if len(history) < required_points:
            return False, f"warmup ({len(history)}/{required_points} baseline points)"

        if current_count < self.settings.min_error_count:
            return False, f"below min error count ({current_count} < {self.settings.min_error_count})"

        last_alert_at = self._last_alert_at.get(service)
        if last_alert_at:
            cooldown = timedelta(minutes=self.settings.alert_cooldown_minutes)
            elapsed = datetime.now(timezone.utc) - last_alert_at
            if elapsed < cooldown:
                remaining_minutes = (cooldown - elapsed).total_seconds() / 60
                return False, f"cooldown active ({remaining_minutes:.1f}m remaining)"

        if baseline <= 0:
            threshold = float(self.settings.min_error_count)
            if current_count >= threshold:
                return True, f"current {current_count} >= threshold {threshold:.1f}"
            return False, f"current {current_count} < threshold {threshold:.1f}"

        threshold = max(
            float(self.settings.min_error_count),
            baseline * self.settings.spike_multiplier,
        )
        if current_count >= threshold:
            return True, f"current {current_count} >= threshold {threshold:.1f}"
        return False, f"current {current_count} < threshold {threshold:.1f}"

    def _percent_spike(self, current_count: int, baseline: float) -> float:
        safe_baseline = max(baseline, 1.0)
        spike = ((current_count - safe_baseline) / safe_baseline) * 100
        return round(spike, 1)
