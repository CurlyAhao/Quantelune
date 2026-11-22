"""Authenticated disclosure/configuration endpoints; no caller-owned dispatch or usage flags."""

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.preview.write_fence import acquire_write_fence
from app.research import saved_replies
from app.schemas.research_security import (
    ApprovalDecision,
    DispatchRequest,
    ProposalCreate,
    ProviderDelete,
    ProviderRecovery,
    ProviderSave,
    ProviderToggle,
)
from app.services import research_provider_service as providers
from app.services import research_security_service as security
from app.services.research_gateway_service import (
    Gateway,
    approval_summary,
    budget_summary,
    get_gateway,
)


class SafeSecurityRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def checked(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Validation input/context can contain credential strings.
                return JSONResponse(
                    status_code=422,
                    content={
                        "error_code": "RESEARCH_SECURITY_INVALID_REQUEST",
                        "detail": "Invalid secure request.",
                    },
                )

        return checked


router = APIRouter(
    prefix="/api/v1/research/security", tags=["research-security"], route_class=SafeSecurityRoute
)


@router.get("/provider")
async def provider_state(
    db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)
):
    return await providers.public(db, user.id)


@router.put("/provider")
async def save_provider(
    body: ProviderSave,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    return await providers.save(db, user.id, body, backend=runtime.vault)


@router.delete("/provider")
async def delete_provider(
    body: ProviderDelete,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    return await providers.delete(db, user.id, body.expected_revision, backend=runtime.vault)


@router.put("/provider/enabled")
async def toggle_provider(
    body: ProviderToggle,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await providers.set_enabled(db, user.id, body.expected_revision, body.enabled)


@router.get("/provider/maintenance")
async def provider_maintenance(
    db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)
):
    return await providers.maintenance(db, user.id)


@router.post("/provider/maintenance/reconcile")
async def reconcile_provider_credentials(
    body: ProviderRecovery,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    return await providers.reconcile_pending(db, user.id, backend=runtime.vault, limit=body.limit)


@router.post("/provider/maintenance/retired/{configuration_id}")
async def cleanup_retired_provider_credential(
    configuration_id: uuid.UUID,
    body: ProviderDelete,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    state, _ = await providers.current(db, user.id)
    if state is None:
        raise providers.failure("CLEANUP_UNAVAILABLE", 404)
    if state.revision != body.expected_revision:
        raise providers.failure("REVISION_CONFLICT")
    status = await providers.cleanup_retired(db, user.id, configuration_id, backend=runtime.vault)
    return {"configuration_id": str(configuration_id), "status": status}


@router.post("/runs/{run_id}/proposals", status_code=201)
async def propose(
    run_id: uuid.UUID,
    body: ProposalCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    return await runtime.propose(db, user.id, run_id, body.proposal_key, body.selection)


@router.get("/approvals/{approval_id}")
async def read_approval(
    approval_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await acquire_write_fence(db, user.id)
    row = await security.approval(db, user.id, approval_id)
    scope = await security._owned_scope(db, user.id, row.run_id)
    await security._registered(db, user.id, scope, row)
    return approval_summary(row)


@router.post("/approvals/{approval_id}/decision")
async def decide(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    return await runtime.decide(
        db, user.id, approval_id, body.request_hash, body.decision, body.selection
    )


@router.post("/dispatch")
async def dispatch(
    body: DispatchRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    return await runtime.dispatch(
        db, user.id, uuid.UUID(body.approval_id), body.request_hash, body.call_key, body.selection
    )


@router.get("/budget")
async def budget(
    db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)
):
    return await budget_summary(db, user.id)


@router.get("/runs/{run_id}/replies")
async def read_saved_replies(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await saved_replies.read(db, user.id, run_id)
