import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from app.schemas import ConversationState


class InvalidConversationTransition(ValueError):
    pass


@dataclass(frozen=True)
class HandoffSnapshot:
    handoff_id: UUID
    reason_code: str
    summary: str | None
    status: str
    requested_by: str | None
    taken_by: str | None
    created_at: datetime
    taken_at: datetime | None


@dataclass(frozen=True)
class ConversationSnapshot:
    conversation_id: UUID
    tenant_slug: str
    contact_id: UUID
    display_name: str | None
    whatsapp_user_id: str
    phone_e164: str | None
    state: ConversationState
    assigned_operator: str | None
    last_state_reason: str | None
    state_changed_at: datetime
    last_message_at: datetime | None
    created_at: datetime
    state_version: int
    active_handoff: HandoffSnapshot | None


@dataclass(frozen=True)
class ConversationTransitionResult:
    changed: bool
    conversation: ConversationSnapshot


_ALLOWED_TRANSITIONS: dict[ConversationState, set[ConversationState]] = {
    ConversationState.AUTOMATED: {
        ConversationState.HUMAN_REQUIRED,
        ConversationState.HUMAN_ACTIVE,
        ConversationState.CLOSED,
    },
    ConversationState.HUMAN_REQUIRED: {
        ConversationState.HUMAN_ACTIVE,
        ConversationState.AUTOMATED,
        ConversationState.CLOSED,
    },
    ConversationState.HUMAN_ACTIVE: {
        ConversationState.AUTOMATED,
        ConversationState.CLOSED,
    },
    ConversationState.CLOSED: set(),
}


def get_conversation(
    *, engine: Engine, tenant_slug: str, conversation_id: UUID
) -> ConversationSnapshot:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        return _load_snapshot(connection, tenant_id, tenant_slug, conversation_id)


def transition_conversation(
    *,
    engine: Engine,
    tenant_slug: str,
    conversation_id: UUID,
    target_state: ConversationState,
    actor: str,
    reason_code: str,
    note: str | None = None,
) -> ConversationTransitionResult:
    actor = actor.strip()
    reason_code = reason_code.strip()
    if not actor:
        raise ValueError("actor is required")
    if not reason_code:
        raise ValueError("reason_code is required")

    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        current_row = connection.execute(
            text(
                """
                SELECT state, assigned_operator, state_version
                FROM conversations
                WHERE id = :conversation_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {"conversation_id": conversation_id, "tenant_id": tenant_id},
        ).mappings().one_or_none()

        if current_row is None:
            raise LookupError(f"Conversation not found: {conversation_id}")

        current_state = ConversationState(current_row["state"])
        if current_state == target_state:
            return ConversationTransitionResult(
                changed=False,
                conversation=_load_snapshot(
                    connection, tenant_id, tenant_slug, conversation_id
                ),
            )

        if target_state not in _ALLOWED_TRANSITIONS[current_state]:
            raise InvalidConversationTransition(
                f"Cannot transition conversation from {current_state} to {target_state}"
            )

        assigned_operator = actor if target_state == ConversationState.HUMAN_ACTIVE else None
        automation_suspended = target_state in {
            ConversationState.HUMAN_REQUIRED,
            ConversationState.HUMAN_ACTIVE,
        }
        closed = target_state == ConversationState.CLOSED

        connection.execute(
            text(
                """
                UPDATE conversations
                SET state = :target_state,
                    assigned_operator = :assigned_operator,
                    last_state_reason = :reason_code,
                    state_changed_at = now(),
                    automation_suspended_at = CASE
                        WHEN :automation_suspended THEN COALESCE(automation_suspended_at, now())
                        ELSE NULL
                    END,
                    closed_at = CASE WHEN :closed THEN now() ELSE NULL END,
                    state_version = state_version + 1
                WHERE id = :conversation_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "target_state": target_state.value,
                "assigned_operator": assigned_operator,
                "reason_code": reason_code,
                "automation_suspended": automation_suspended,
                "closed": closed,
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
            },
        )

        _synchronize_handoff(
            connection=connection,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            target_state=target_state,
            actor=actor,
            reason_code=reason_code,
            note=note,
        )

        connection.execute(
            text(
                """
                INSERT INTO audit_events (
                    tenant_id,
                    conversation_id,
                    event_type,
                    decision,
                    payload
                )
                VALUES (
                    :tenant_id,
                    :conversation_id,
                    'CONVERSATION_STATE_CHANGED',
                    :decision,
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "decision": target_state.value,
                "payload": json.dumps(
                    {
                        "from_state": current_state.value,
                        "to_state": target_state.value,
                        "actor": actor,
                        "reason_code": reason_code,
                        "note": note,
                    },
                    ensure_ascii=False,
                ),
            },
        )

        snapshot = _load_snapshot(connection, tenant_id, tenant_slug, conversation_id)
        return ConversationTransitionResult(changed=True, conversation=snapshot)


def _synchronize_handoff(
    *,
    connection: Connection,
    tenant_id: UUID,
    conversation_id: UUID,
    target_state: ConversationState,
    actor: str,
    reason_code: str,
    note: str | None,
) -> None:
    active_handoff_id = connection.execute(
        text(
            """
            SELECT id
            FROM handoffs
            WHERE tenant_id = :tenant_id
              AND conversation_id = :conversation_id
              AND status IN ('OPEN', 'TAKEN')
            ORDER BY created_at DESC
            LIMIT 1
            FOR UPDATE
            """
        ),
        {"tenant_id": tenant_id, "conversation_id": conversation_id},
    ).scalar_one_or_none()

    if target_state == ConversationState.HUMAN_REQUIRED:
        if active_handoff_id is None:
            connection.execute(
                text(
                    """
                    INSERT INTO handoffs (
                        tenant_id,
                        conversation_id,
                        reason_code,
                        summary,
                        status,
                        requested_by
                    )
                    VALUES (
                        :tenant_id,
                        :conversation_id,
                        :reason_code,
                        :summary,
                        'OPEN',
                        :requested_by
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "reason_code": reason_code,
                    "summary": note,
                    "requested_by": actor,
                },
            )
        return

    if target_state == ConversationState.HUMAN_ACTIVE:
        if active_handoff_id is None:
            connection.execute(
                text(
                    """
                    INSERT INTO handoffs (
                        tenant_id,
                        conversation_id,
                        reason_code,
                        summary,
                        status,
                        requested_by,
                        taken_by,
                        taken_at
                    )
                    VALUES (
                        :tenant_id,
                        :conversation_id,
                        :reason_code,
                        :summary,
                        'TAKEN',
                        :actor,
                        :actor,
                        now()
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "reason_code": reason_code,
                    "summary": note,
                    "actor": actor,
                },
            )
        else:
            connection.execute(
                text(
                    """
                    UPDATE handoffs
                    SET status = 'TAKEN',
                        taken_by = :actor,
                        taken_at = COALESCE(taken_at, now())
                    WHERE id = :handoff_id
                    """
                ),
                {"actor": actor, "handoff_id": active_handoff_id},
            )
        return

    if target_state in {ConversationState.AUTOMATED, ConversationState.CLOSED}:
        connection.execute(
            text(
                """
                UPDATE handoffs
                SET status = 'RESOLVED',
                    resolved_at = now(),
                    resolved_by = :actor,
                    resolution_note = :note
                WHERE tenant_id = :tenant_id
                  AND conversation_id = :conversation_id
                  AND status IN ('OPEN', 'TAKEN')
                """
            ),
            {
                "actor": actor,
                "note": note,
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
            },
        )


def _get_active_tenant_id(connection: Connection, tenant_slug: str) -> UUID:
    tenant_id = connection.execute(
        text("SELECT id FROM tenants WHERE slug = :slug AND status = 'active'"),
        {"slug": tenant_slug},
    ).scalar_one_or_none()
    if tenant_id is None:
        raise LookupError(f"Active tenant not found: {tenant_slug}")
    return tenant_id


def _load_snapshot(
    connection: Connection,
    tenant_id: UUID,
    tenant_slug: str,
    conversation_id: UUID,
) -> ConversationSnapshot:
    row = connection.execute(
        text(
            """
            SELECT
                c.id AS conversation_id,
                c.contact_id,
                ct.display_name,
                ct.whatsapp_user_id,
                ct.phone_e164,
                c.state,
                c.assigned_operator,
                c.last_state_reason,
                c.state_changed_at,
                c.last_message_at,
                c.created_at,
                c.state_version,
                h.id AS handoff_id,
                h.reason_code AS handoff_reason_code,
                h.summary AS handoff_summary,
                h.status AS handoff_status,
                h.requested_by AS handoff_requested_by,
                h.taken_by AS handoff_taken_by,
                h.created_at AS handoff_created_at,
                h.taken_at AS handoff_taken_at
            FROM conversations c
            JOIN contacts ct ON ct.id = c.contact_id
            LEFT JOIN LATERAL (
                SELECT *
                FROM handoffs h
                WHERE h.conversation_id = c.id
                  AND h.status IN ('OPEN', 'TAKEN')
                ORDER BY h.created_at DESC
                LIMIT 1
            ) h ON true
            WHERE c.id = :conversation_id
              AND c.tenant_id = :tenant_id
            """
        ),
        {"conversation_id": conversation_id, "tenant_id": tenant_id},
    ).mappings().one_or_none()

    if row is None:
        raise LookupError(f"Conversation not found: {conversation_id}")

    handoff = None
    if row["handoff_id"] is not None:
        handoff = HandoffSnapshot(
            handoff_id=row["handoff_id"],
            reason_code=row["handoff_reason_code"],
            summary=row["handoff_summary"],
            status=row["handoff_status"],
            requested_by=row["handoff_requested_by"],
            taken_by=row["handoff_taken_by"],
            created_at=row["handoff_created_at"],
            taken_at=row["handoff_taken_at"],
        )

    return ConversationSnapshot(
        conversation_id=row["conversation_id"],
        tenant_slug=tenant_slug,
        contact_id=row["contact_id"],
        display_name=row["display_name"],
        whatsapp_user_id=row["whatsapp_user_id"],
        phone_e164=row["phone_e164"],
        state=ConversationState(row["state"]),
        assigned_operator=row["assigned_operator"],
        last_state_reason=row["last_state_reason"],
        state_changed_at=row["state_changed_at"],
        last_message_at=row["last_message_at"],
        created_at=row["created_at"],
        state_version=row["state_version"],
        active_handoff=handoff,
    )
