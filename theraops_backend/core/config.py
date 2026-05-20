from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)
PERSISTENT_VOLUME_PREFIXES = ("/data", "/mnt", "/volumes")


def _load_env_files() -> None:
    load_dotenv(REPO_ROOT / ".env", override=False)
    load_dotenv(REPO_ROOT / "theraops_backend" / ".env", override=False)
    load_dotenv(override=False)


_load_env_files()


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    graylog_url: str
    graylog_token: str
    slack_bot_token: str
    alert_channel_id: str
    gemini_api_key: str
    gemini_model: str
    internal_api_token: str
    custom_llm_url: str
    ngrok_llm_model: str
    llm_model: str
    watch_services: list[str]
    poll_interval_seconds: int
    error_window_seconds: int
    baseline_window_count: int
    spike_multiplier: float
    min_error_count: int
    alert_cooldown_minutes: int
    run_watcher_in_api: bool
    incidents_file: Path

    @property
    def watcher_enabled(self) -> bool:
        return bool(
            self.watch_services
            and self.graylog_url
            and self.graylog_token
            and self.slack_bot_token
            and self.alert_channel_id
        )


def validate_incident_memory_path(settings: Settings) -> Path:
    incidents_file = settings.incidents_file.expanduser()
    resolved_path = incidents_file.resolve(strict=False)
    parent_dir = resolved_path.parent

    if not parent_dir.exists():
        raise RuntimeError(
            "INCIDENT_MEMORY_FILE directory does not exist: "
            f"{parent_dir} (from {settings.incidents_file})"
        )

    if not parent_dir.is_dir():
        raise RuntimeError(
            "INCIDENT_MEMORY_FILE parent is not a directory: "
            f"{parent_dir} (from {settings.incidents_file})"
        )

    if not os.access(parent_dir, os.W_OK):
        raise RuntimeError(
            "INCIDENT_MEMORY_FILE directory is not writable: "
            f"{parent_dir} (from {settings.incidents_file})"
        )

    try:
        with tempfile.NamedTemporaryFile(dir=parent_dir, prefix=".frieren-write-check-", delete=True):
            pass
    except OSError as exc:
        raise RuntimeError(
            "INCIDENT_MEMORY_FILE directory failed a write test: "
            f"{parent_dir} (from {settings.incidents_file})"
        ) from exc

    path_for_heuristic = resolved_path.as_posix()
    if path_for_heuristic.startswith("/") and not path_for_heuristic.startswith(PERSISTENT_VOLUME_PREFIXES):
        logger.warning(
            "Frieren memory appears to be on the container filesystem, not a mounted volume: %s. "
            "Use a persistent mount such as /data, /mnt, or /volumes.",
            resolved_path,
        )

    logger.info("Frieren memory: OK (%s)", resolved_path)
    return resolved_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    default_incident_file = REPO_ROOT / "theraops_backend" / "data" / "incidents.json"

    return Settings(
        graylog_url=os.getenv("GRAYLOG_URL", "").rstrip("/"),
        graylog_token=os.getenv("GRAYLOG_TOKEN", ""),
        slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
        alert_channel_id=os.getenv("ALERT_CHANNEL_ID", ""),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        internal_api_token=os.getenv("THERAOPS_INTERNAL_API_TOKEN", "").strip(),
        custom_llm_url=os.getenv("CUSTOM_LLM_URL", ""),
        ngrok_llm_model=os.getenv("NGROK_LLM_MODEL", ""),
        llm_model=os.getenv("LLM_MODEL", "google/gemma-2b"),
        watch_services=_split_csv(os.getenv("WATCH_SERVICES")),
        poll_interval_seconds=int(os.getenv("WATCH_POLL_SECONDS")),
        error_window_seconds=int(os.getenv("GRAYLOG_ERROR_WINDOW_SECONDS")),
        baseline_window_count=int(os.getenv("WATCH_BASELINE_WINDOWS")),
        spike_multiplier=float(os.getenv("WATCH_SPIKE_MULTIPLIER")),
        min_error_count=int(os.getenv("WATCH_MIN_ERROR_COUNT", "1")),
        alert_cooldown_minutes=int(os.getenv("WATCH_ALERT_COOLDOWN_MINUTES")),
        run_watcher_in_api=_get_bool("RUN_WATCHER_IN_API", False),
        incidents_file=Path(os.getenv("INCIDENT_MEMORY_FILE", default_incident_file)),
    )
