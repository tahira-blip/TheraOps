import asyncio
from unittest.mock import AsyncMock
from theraops_backend.core.config import get_settings
from theraops_backend.monitoring.fern_watcher import FernWatcher, GraylogClient, SlackNotifier, ErrorSummary

class MockMentor:
    def format_spike_alert(self, service, percent_spike, error_count, sample_message, baseline=0.0, window_seconds=300, cooldown_minutes=15):
        window_min = window_seconds // 60
        baseline_display = f"{baseline:.0f}" if baseline > 0 else "0"
        return (
            f":rotating_light: *TEST ALERT — Error spike in `{service}`*\n"
            f"Spike: *{percent_spike:.1f}% above baseline* ({error_count} → {baseline_display})\n"
            f"Time window: last {window_min} min\n"
            f"Sample: `{sample_message}`\n"
            f"Suggested action: `/thera logs {service}`\n"
            f"Cool down: {cooldown_minutes} min remaining"
        )

async def main():
    settings = get_settings()

    # 1. Mock Graylog to return 0 errors for the baseline, then suddenly 15 errors
    mock_graylog = GraylogClient(settings.graylog_url, settings.graylog_token)
    mock_graylog.fetch_error_summary = AsyncMock(side_effect=[
        ErrorSummary(service="vianapulse", error_count=0, sample_messages=[], status_code=200),
        ErrorSummary(service="vianapulse", error_count=0, sample_messages=[], status_code=200),
        ErrorSummary(service="vianapulse", error_count=0, sample_messages=[], status_code=200),
        ErrorSummary(service="vianapulse", error_count=15, sample_messages=["Unexpected HRESULT has been returned from a call to a COM component"], status_code=200),
    ])

    # 2. Use real Slack Notifier
    notifier = SlackNotifier(settings.slack_bot_token)
    
    watcher = FernWatcher(settings, mock_graylog, notifier, MockMentor())

    print("Simulating background watcher polling...")
    for i in range(4):
        print(f"Poll {i+1}...")
        decision = await watcher.evaluate_service("vianapulse")
        if decision.should_alert:
            await watcher._handle_alert_decision(decision)
        else:
            watcher.record_decision(decision, alerted=False)

    print("\nDid you receive a Slack Alert?")
    await notifier.close()

if __name__ == "__main__":
    asyncio.run(main())
