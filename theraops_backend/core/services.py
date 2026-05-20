from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastapi import Request

if TYPE_CHECKING:
    from theraops_backend.core.config import Settings
    from theraops_backend.interface.flamme_mentor import FlammeMentor
    from theraops_backend.memory.frieren_librarian import FrierenLibrarian
    from theraops_backend.monitoring.fern_watcher import FernWatcher, GraylogClient, SlackNotifier


@dataclass(frozen=True)
class AppServices:
    settings: Settings
    graylog: GraylogClient
    memory: FrierenLibrarian
    mentor: FlammeMentor
    slack_notifier: SlackNotifier
    watcher: FernWatcher


def get_services(request: Request) -> AppServices:
    return cast(AppServices, request.app.state.services)
