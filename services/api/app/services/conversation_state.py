from uuid import UUID

from sqlalchemy import Engine

from app.repositories.conversations import (
    ConversationSnapshot,
    ConversationTransitionResult,
    get_conversation,
    transition_conversation,
)
from app.schemas import ConversationState


def read_conversation(
    *, engine: Engine, tenant_slug: str, conversation_id: UUID
) -> ConversationSnapshot:
    return get_conversation(
        engine=engine,
        tenant_slug=tenant_slug,
        conversation_id=conversation_id,
    )


def request_handoff(
    *,
    engine: Engine,
    tenant_slug: str,
    conversation_id: UUID,
    requested_by: str,
    reason_code: str,
    summary: str | None,
) -> ConversationTransitionResult:
    return transition_conversation(
        engine=engine,
        tenant_slug=tenant_slug,
        conversation_id=conversation_id,
        target_state=ConversationState.HUMAN_REQUIRED,
        actor=requested_by,
        reason_code=reason_code,
        note=summary,
    )


def take_conversation(
    *,
    engine: Engine,
    tenant_slug: str,
    conversation_id: UUID,
    operator_name: str,
    note: str | None,
) -> ConversationTransitionResult:
    return transition_conversation(
        engine=engine,
        tenant_slug=tenant_slug,
        conversation_id=conversation_id,
        target_state=ConversationState.HUMAN_ACTIVE,
        actor=operator_name,
        reason_code="operator_takeover",
        note=note,
    )


def return_to_automation(
    *,
    engine: Engine,
    tenant_slug: str,
    conversation_id: UUID,
    operator_name: str,
    note: str | None,
) -> ConversationTransitionResult:
    return transition_conversation(
        engine=engine,
        tenant_slug=tenant_slug,
        conversation_id=conversation_id,
        target_state=ConversationState.AUTOMATED,
        actor=operator_name,
        reason_code="returned_to_automation",
        note=note,
    )


def close_conversation(
    *,
    engine: Engine,
    tenant_slug: str,
    conversation_id: UUID,
    operator_name: str,
    note: str | None,
) -> ConversationTransitionResult:
    return transition_conversation(
        engine=engine,
        tenant_slug=tenant_slug,
        conversation_id=conversation_id,
        target_state=ConversationState.CLOSED,
        actor=operator_name,
        reason_code="conversation_closed",
        note=note,
    )
