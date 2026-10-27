"""FastAPI routing and endpoints for the WorkPilot AI Assistant."""

from __future__ import annotations

from typing import Any
import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.database import get_db, get_redis
from src.integrations.orchestration.provider import ProviderError
from src.integrations.orchestration.schemas import (
    ActionConfirmationRequest,
    ActionConfirmationResponseData,
    ChatRequest,
    ChatResponseData,
)
from src.integrations.orchestration.service import AIOrchestrationService
from src.utils.response import error, success
from src.task.service import TaskServiceError
from src.utils.core import require_jwt
from src.utils.security import rate_limit

logger = get_logger(__name__)

router = APIRouter(prefix="/ai-assistant", tags=["AI Assistant"])


def get_ai_service(
    db: AsyncSession = Depends(get_db),
    redis: Any = Depends(get_redis),
) -> AIOrchestrationService:
    return AIOrchestrationService(db, redis)


def _auth_context(request: Request) -> tuple[str, str, str]:
    user_id = getattr(request.state, "user_id", None)
    organization_id = getattr(request.state, "organization_id", None)
    role = getattr(request.state, "role", "") or ""

    if not user_id:
        raise TaskServiceError(401, "UNAUTHORIZED", "Authentication required")
    if not organization_id:
        raise TaskServiceError(403, "ORGANIZATION_REQUIRED", "Organization is required")

    try:
        user_uuid = str(uuid.UUID(str(user_id)))
        org_uuid = str(uuid.UUID(str(organization_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TaskServiceError(400, "BAD_REQUEST", "Invalid ID format") from exc

    return user_uuid, org_uuid, str(role)


def _handle_exception(exc: Exception):
    if isinstance(exc, TaskServiceError):
        return error(exc.message, status_code=exc.status_code, code=exc.code)
    if isinstance(exc, ProviderError):
        return error(exc.message, status_code=exc.status_code, code=exc.code)
    logger.error("Unhandled AI Assistant exception: %s", exc, exc_info=True)
    return error("Something went wrong. Please try again later.", status_code=500, code="INTERNAL_SERVER_ERROR")


@router.post(
    "/chat",
    status_code=status.HTTP_200_OK,
    summary="Chat with the WorkPilot AI Assistant",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60, scope="ai_chat"))],
)
@require_jwt
async def chat_with_assistant(
    request: Request,
    body: ChatRequest,
    service: AIOrchestrationService = Depends(get_ai_service),
):
    """Receive natural-language instructions and propose or execute task actions."""
    try:
        user_id, organization_id, role = _auth_context(request)
        response_data = await service.process_chat(
            body,
            user_id=user_id,
            organization_id=organization_id,
            role=role,
        )
        return success(
            "AI response generated successfully",
            response_data.model_dump(),
            status_code=200,
        )
    except Exception as exc:
        return _handle_exception(exc)


@router.post(
    "/actions/{action_id}/confirm",
    status_code=status.HTTP_200_OK,
    summary="Confirm or cancel a pending AI action",
)
@router.post(
    "/confirm/{action_id}",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@require_jwt
async def confirm_action(
    action_id: str,
    request: Request,
    body: ActionConfirmationRequest | None = None,
    confirm: bool | None = None,
    confirmed: bool | None = None,
    service: AIOrchestrationService = Depends(get_ai_service),
):
    """Confirm or cancel a pending server-side action: pass true to execute task creation, or false to cancel."""
    try:
        user_id, organization_id, role = _auth_context(request)

        is_confirmed = True
        if confirm is not None:
            is_confirmed = confirm
        elif confirmed is not None:
            is_confirmed = confirmed
        elif body is not None:
            is_confirmed = body.is_confirmed

        if not is_confirmed:
            res = await service.cancel_action(
                action_id,
                user_id=user_id,
                organization_id=organization_id,
            )
            return success("Action cancelled successfully", res, status_code=200)

        res = await service.confirm_action(
            action_id,
            user_id=user_id,
            organization_id=organization_id,
            role=role,
        )
        msg = res.get("message") or "Action completed successfully"
        return success(msg, res, status_code=200)
    except Exception as exc:
        return _handle_exception(exc)
