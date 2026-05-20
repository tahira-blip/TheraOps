from __future__ import annotations

from theraops_backend.core.config import get_settings
from theraops_backend.core.services import AppServices
from theraops_backend.interface.flamme_mentor import FlammeMentor
from theraops_backend.memory.frieren_librarian import FrierenLibrarian
from theraops_backend.monitoring.fern_watcher import FernWatcher, GraylogClient, SlackNotifier


def build_services() -> AppServices:
    settings = get_settings()
    graylog = GraylogClient(settings.graylog_url, settings.graylog_token)
    memory = FrierenLibrarian(settings.incidents_file)
    mentor = FlammeMentor(settings)
    slack_notifier = SlackNotifier(settings.slack_bot_token)
    watcher = FernWatcher(settings, graylog, slack_notifier, mentor)

    return AppServices(
        settings=settings,
        graylog=graylog,
        memory=memory,
        mentor=mentor,
        slack_notifier=slack_notifier,
        watcher=watcher,
    )


async def close_services(services: AppServices) -> None:
    await services.graylog.close()
    await services.mentor.close()
    await services.slack_notifier.close()
