from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from theraops_backend.core.services import get_services


async def verify_internal_request(
    request: Request,
    x_internal_api_token: str | None = Header(default=None),
) -> None:
    settings = get_services(request).settings

    if not settings.internal_api_token:
        return

    if x_internal_api_token != settings.internal_api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal API token.",
        )
