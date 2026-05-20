from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from theraops_backend.monitoring.fern_watcher import AlertDecision
from theraops_backend.runtime import build_services, close_services

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
DRY_RUN_DURATION_SECONDS = 30 * 60


async def run_watcher() -> None:
    services = build_services()

    try:
        if not services.settings.watcher_enabled:
            logger.info("Watcher is disabled. Check Graylog, Slack, and watch service settings.")
            return

        logger.info("Starting watcher as a standalone process.")
        await services.watcher.run_forever()
    finally:
        services.watcher.stop()
        await close_services(services)


def _format_dry_run_line(decision: AlertDecision) -> str:
    baseline_text = "warmup" if decision.is_warmup else f"{decision.baseline:.1f}"
    alert_text = "⚠️ WOULD ALERT" if decision.should_alert else "no"
    return (
        f"{decision.service} | baseline: {baseline_text} | current: {decision.current_count} | "
        f"would alert: {alert_text} | reason: {decision.reason}"
    )


async def run_watcher_dry_run() -> None:
    services = build_services()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DRY_RUN_DURATION_SECONDS

    try:
        if not services.settings.watcher_enabled:
            logger.info("Watcher is disabled. Check Graylog, Slack, and watch service settings.")
            return

        logger.info(
            "Starting watcher dry-run for %s minutes. Poll interval: %s seconds.",
            DRY_RUN_DURATION_SECONDS // 60,
            services.settings.poll_interval_seconds,
        )

        while loop.time() < deadline:
            print(f"\n[{datetime.now(timezone.utc).isoformat()}] Dry-run poll")
            decisions = await services.watcher.collect_decisions()

            for service, result in zip(services.settings.watch_services, decisions):
                if isinstance(result, Exception):
                    print(f"{service} | ERROR | reason: {result!r}")
                    continue

                print(_format_dry_run_line(result))
                services.watcher.record_decision(result, alerted=result.should_alert)

            remaining = deadline - loop.time()
            if remaining <= 0:
                break

            await asyncio.sleep(min(services.settings.poll_interval_seconds, remaining))
    finally:
        services.watcher.stop()
        await close_services(services)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the TheraOps watcher.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Poll Graylog for 30 minutes and print alert decisions instead of posting to Slack.",
    )
    args = parser.parse_args(argv)

    try:
        asyncio.run(run_watcher_dry_run() if args.dry_run else run_watcher())
    except KeyboardInterrupt:
        logger.info("Watcher stopped.")


if __name__ == "__main__":
    main()
