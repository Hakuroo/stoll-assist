from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from app.auth import READ_ROLES, AuthContext, require_roles
from app.database import get_engine
from app.repositories.operator_dashboard import (
    get_dashboard_conversation_detail,
    list_dashboard_conversations,
    list_dashboard_outbox_review,
)
from app.schemas import (
    ConversationState,
    DashboardConversationDetailResponse,
    DashboardConversationSummaryResponse,
    DashboardOutboxReviewItemResponse,
)
router = APIRouter(prefix="/operator/dashboard", tags=["operator-dashboard"])


@router.get("/conversations", response_model=list[DashboardConversationSummaryResponse])
def conversations(
    state_filter: ConversationState | None = Query(default=None, alias="state"),
    limit: int = Query(default=100, ge=1, le=200),
    auth: AuthContext = Depends(require_roles(*READ_ROLES)),
) -> list[DashboardConversationSummaryResponse]:
    try:
        items = list_dashboard_conversations(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            state_filter=state_filter,
            limit=limit,
        )
        return [DashboardConversationSummaryResponse.from_item(item) for item in items]
    except (LookupError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard conversations could not be listed",
        ) from exc


@router.get(
    "/conversations/{conversation_id}",
    response_model=DashboardConversationDetailResponse,
)
def conversation_detail(
    conversation_id: UUID,
    auth: AuthContext = Depends(require_roles(*READ_ROLES)),
) -> DashboardConversationDetailResponse:
    try:
        detail = get_dashboard_conversation_detail(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            conversation_id=conversation_id,
        )
        return DashboardConversationDetailResponse.from_detail(detail)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard conversation could not be read",
        ) from exc


@router.get("/outbox", response_model=list[DashboardOutboxReviewItemResponse])
def outbox_review(
    limit: int = Query(default=100, ge=1, le=200),
    auth: AuthContext = Depends(require_roles(*READ_ROLES)),
) -> list[DashboardOutboxReviewItemResponse]:
    try:
        items = list_dashboard_outbox_review(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            status_filter="PENDING_REVIEW",
            limit=limit,
        )
        return [DashboardOutboxReviewItemResponse.from_item(item) for item in items]
    except (LookupError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard outbox could not be listed",
        ) from exc
