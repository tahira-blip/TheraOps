from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env", override=False)

from theraops_backend.core.config import get_settings
from theraops_backend.monitoring.fern_watcher import GraylogClient


def _print_success(service: str, status_code: int, error_count: int, sample_messages: list[str]) -> None:
    print(f"service: {service}")
    print(f"status_code: {status_code}")
    print(f"error_count: {error_count}")
    print("sample_messages:")

    if not sample_messages:
        print("  - <none>")
    else:
        for message in sample_messages[:3]:
            print(f"  - {message}")


def _print_failure(service: str, exc: Exception) -> None:
    print(f"service: {service}")

    if isinstance(exc, httpx.HTTPStatusError):
        print(f"status_code: {exc.response.status_code}")
        print("error_response_body:")
        print(exc.response.text)
        return

    if isinstance(exc, httpx.RequestError):
        print("status_code: <no response>")
        print("error_response_body:")
        print(str(exc))
        return

    print("status_code: <unknown>")
    print("error_response_body:")
    print(repr(exc))


async def main() -> int:
    settings = get_settings()
    graylog = GraylogClient(settings.graylog_url, settings.graylog_token)
    had_failure = False

    try:
        for service in settings.watch_services:
            print(f"--- {service} ---")
            try:
                summary = await graylog.fetch_error_summary(
                    service=service,
                    window_seconds=settings.error_window_seconds,
                    limit=3,
                )
                _print_success(
                    service=summary.service,
                    status_code=summary.status_code,
                    error_count=summary.error_count,
                    sample_messages=summary.sample_messages,
                )
            except Exception as exc:
                had_failure = True
                _print_failure(service, exc)
    finally:
        await graylog.close()

    return 1 if had_failure else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
