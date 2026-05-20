from __future__ import annotations

from fastapi import APIRouter, Request

from theraops_backend.core.services import get_services

router = APIRouter(tags=["system"])


@router.get("/health")
async def healthcheck(request: Request) -> dict[str, object]:
    settings = get_services(request).settings
    return {
        "status": "ok",
        "watch_services": settings.watch_services,
        "watcher_enabled": settings.watcher_enabled,
        "run_watcher_in_api": settings.run_watcher_in_api,
        "internal_api_auth_enabled": bool(settings.internal_api_token),
    }
