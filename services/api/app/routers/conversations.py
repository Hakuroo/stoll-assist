from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.auth import OPERATE_ROLES, READ_ROLES, AuthContext, require_roles
from app.database import get_engine
from app.repositories.conversations import InvalidConversationTransition
from app.schemas import (
    ConversationResponse,
    HandoffRequest,
    OperatorActionRequest,
    StateTransitionResponse,
)
from app.services.conversation_state import (
    close_conversation,
    read_conversation,
    request_handoff,
    return_to_automation,
    take_conversation,
)
router = APIRouter(prefix="/operator/conversations", tags=["operator-conversations"])


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: UUID,
    auth: AuthContext = Depends(require_roles(*READ_ROLES)),
) -> ConversationResponse:
    try:
        snapshot = read_conversation(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            conversation_id=conversation_id,
        )
        return ConversationResponse.from_snapshot(snapshot)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Conversation could not be read") from exc


@router.post(
    "/{conversation_id}/request-handoff",
    response_model=StateTransitionResponse,
)
def create_handoff(
    conversation_id: UUID,
    request: HandoffRequest,
    auth: AuthContext = Depends(require_roles(*OPERATE_ROLES, csrf=True)),
) -> StateTransitionResponse:
    return _execute_transition(
        lambda: request_handoff(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            conversation_id=conversation_id,
            requested_by=auth.display_name,
            reason_code=request.reason_code,
            summary=request.summary,
        )
    )


@router.post("/{conversation_id}/take", response_model=StateTransitionResponse)
def take(
    conversation_id: UUID,
    request: OperatorActionRequest,
    auth: AuthContext = Depends(require_roles(*OPERATE_ROLES, csrf=True)),
) -> StateTransitionResponse:
    return _execute_transition(
        lambda: take_conversation(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            conversation_id=conversation_id,
            operator_name=auth.display_name,
            note=request.note,
        )
    )


@router.post(
    "/{conversation_id}/return-to-automation",
    response_model=StateTransitionResponse,
)
def return_automation(
    conversation_id: UUID,
    request: OperatorActionRequest,
    auth: AuthContext = Depends(require_roles(*OPERATE_ROLES, csrf=True)),
) -> StateTransitionResponse:
    return _execute_transition(
        lambda: return_to_automation(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            conversation_id=conversation_id,
            operator_name=auth.display_name,
            note=request.note,
        )
    )


@router.post("/{conversation_id}/close", response_model=StateTransitionResponse)
def close(
    conversation_id: UUID,
    request: OperatorActionRequest,
    auth: AuthContext = Depends(require_roles(*OPERATE_ROLES, csrf=True)),
) -> StateTransitionResponse:
    return _execute_transition(
        lambda: close_conversation(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            conversation_id=conversation_id,
            operator_name=auth.display_name,
            note=request.note,
        )
    )


def _execute_transition(action) -> StateTransitionResponse:
    try:
        result = action()
        return StateTransitionResponse(
            changed=result.changed,
            conversation=ConversationResponse.from_snapshot(result.conversation),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidConversationTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation state could not be changed",
        ) from exc
