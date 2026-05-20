from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from theraops_backend.core.auth import verify_internal_request
from theraops_backend.core.services import get_services

router = APIRouter(
    prefix="/incidents",
    tags=["incidents"],
    dependencies=[Depends(verify_internal_request)],
)


class ResolveIncidentRequest(BaseModel):
    service: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    fix: str = Field(min_length=1)


@router.post("/resolve")
async def resolve_incident(payload: ResolveIncidentRequest, request: Request) -> dict[str, object]:
    memory = get_services(request).memory
    incident = await memory.store_incident(
        service=payload.service,
        root_cause=payload.root_cause,
        fix=payload.fix,
    )
    return {"status": "stored", "incident": incident.__dict__}
