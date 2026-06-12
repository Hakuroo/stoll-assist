import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from app.normalization import NormalizedInboundMessage


@dataclass(frozen=True)
class PersistedInboundMessage:
    message_id: UUID
    conversation_id: UUID
    provider_message_id: str
    message_type: str
    body_text: str | None


@dataclass(frozen=True)
class ConversationMessage:
    message_id: UUID
    direction: str
    message_type: str
    body_text: str | None
    created_at: datetime


def persist_inbound_messages(
    *,
    engine: Engine,
    tenant_slug: str,
    messages: Sequence[NormalizedInboundMessage],
) -> int:
    return len(
        persist_inbound_messages_with_context(
            engine=engine,
            tenant_slug=tenant_slug,
            messages=messages,
        )
    )


def persist_inbound_messages_with_context(
    *,
    engine: Engine,
    tenant_slug: str,
    messages: Sequence[NormalizedInboundMessage],
    include_existing: bool = False,
) -> list[PersistedInboundMessage]:
    if not messages:
        return []

    persisted: list[PersistedInboundMessage] = []
    seen_provider_ids: set[str] = set()

    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)

        for message in messages:
            if message.provider_message_id in seen_provider_ids:
                continue
            seen_provider_ids.add(message.provider_message_id)

            contact_id = _upsert_contact(connection, tenant_id, message)
            conversation_id = _get_or_create_active_conversation(
                connection,
                tenant_id,
                contact_id,
            )

            inserted = connection.execute(
                text(
                    """
                    INSERT INTO messages (
                        tenant_id,
                        conversation_id,
                        provider_message_id,
                        direction,
                        message_type,
                        body_text,
                        provider_timestamp,
                        metadata,
                        raw_payload
                    )
                    VALUES (
                        :tenant_id,
                        :conversation_id,
                        :provider_message_id,
                        'INBOUND',
                        :message_type,
                        :body_text,
                        :provider_timestamp,
                        CAST(:metadata AS jsonb),
                        CAST(:raw_payload AS jsonb)
                    )
                    ON CONFLICT (tenant_id, provider_message_id) DO NOTHING
                    RETURNING id, conversation_id, provider_message_id, message_type, body_text
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "provider_message_id": message.provider_message_id,
                    "message_type": message.message_type,
                    "body_text": message.body_text,
                    "provider_timestamp": message.provider_timestamp,
                    "metadata": json.dumps(message.metadata, ensure_ascii=False),
                    "raw_payload": json.dumps(message.raw_message, ensure_ascii=False),
                },
            ).mappings().one_or_none()

            if inserted is None:
                if include_existing:
                    existing = _get_existing_inbound_message(
                        connection,
                        tenant_id,
                        message.provider_message_id,
                    )
                    if existing is not None:
                        persisted.append(existing)
                continue

            persisted.append(
                PersistedInboundMessage(
                    message_id=inserted["id"],
                    conversation_id=inserted["conversation_id"],
                    provider_message_id=inserted["provider_message_id"],
                    message_type=inserted["message_type"],
                    body_text=inserted["body_text"],
                )
            )

            connection.execute(
                text(
                    """
                    UPDATE conversations
                    SET last_message_at = CASE
                        WHEN :message_timestamp IS NULL THEN COALESCE(last_message_at, now())
                        WHEN last_message_at IS NULL THEN :message_timestamp
                        ELSE GREATEST(last_message_at, :message_timestamp)
                    END
                    WHERE id = :conversation_id
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "message_timestamp": message.provider_timestamp,
                },
            )

    return persisted


def _get_existing_inbound_message(
    connection: Connection,
    tenant_id: UUID,
    provider_message_id: str,
) -> PersistedInboundMessage | None:
    row = connection.execute(
        text(
            """
            SELECT id, conversation_id, provider_message_id, message_type, body_text
            FROM messages
            WHERE tenant_id = :tenant_id
              AND provider_message_id = :provider_message_id
              AND direction = 'INBOUND'
            """
        ),
        {
            "tenant_id": tenant_id,
            "provider_message_id": provider_message_id,
        },
    ).mappings().one_or_none()

    if row is None:
        return None

    return PersistedInboundMessage(
        message_id=row["id"],
        conversation_id=row["conversation_id"],
        provider_message_id=row["provider_message_id"],
        message_type=row["message_type"],
        body_text=row["body_text"],
    )


def list_recent_conversation_messages(
    *,
    engine: Engine,
    tenant_slug: str,
    conversation_id: UUID,
    limit: int = 6,
) -> list[ConversationMessage]:
    limit = min(max(limit, 1), 12)
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        rows = connection.execute(
            text(
                """
                SELECT id, direction, message_type, body_text, created_at
                FROM messages
                WHERE tenant_id = :tenant_id
                  AND conversation_id = :conversation_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "limit": limit,
            },
        ).mappings().all()

    return [
        ConversationMessage(
            message_id=row["id"],
            direction=row["direction"],
            message_type=row["message_type"],
            body_text=row["body_text"],
            created_at=row["created_at"],
        )
        for row in reversed(rows)
    ]


def _get_active_tenant_id(connection: Connection, tenant_slug: str) -> UUID:
    tenant_id = connection.execute(
        text("SELECT id FROM tenants WHERE slug = :slug AND status = 'active'"),
        {"slug": tenant_slug},
    ).scalar_one_or_none()

    if tenant_id is None:
        raise LookupError(f"Active tenant not found: {tenant_slug}")

    return tenant_id


def _upsert_contact(
    connection: Connection,
    tenant_id: UUID,
    message: NormalizedInboundMessage,
) -> UUID:
    return connection.execute(
        text(
            """
            INSERT INTO contacts (
                tenant_id,
                whatsapp_user_id,
                display_name,
                phone_e164
            )
            VALUES (
                :tenant_id,
                :whatsapp_user_id,
                :display_name,
                :phone_e164
            )
            ON CONFLICT (tenant_id, whatsapp_user_id)
            DO UPDATE SET
                display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''), contacts.display_name),
                phone_e164 = COALESCE(EXCLUDED.phone_e164, contacts.phone_e164)
            RETURNING id
            """
        ),
        {
            "tenant_id": tenant_id,
            "whatsapp_user_id": message.sender_wa_id,
            "display_name": message.sender_name,
            "phone_e164": message.phone_e164,
        },
    ).scalar_one()


def _get_or_create_active_conversation(
    connection: Connection,
    tenant_id: UUID,
    contact_id: UUID,
) -> UUID:
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"{tenant_id}:{contact_id}"},
    )

    conversation_id = connection.execute(
        text(
            """
            SELECT id
            FROM conversations
            WHERE tenant_id = :tenant_id
              AND contact_id = :contact_id
              AND state <> 'CLOSED'
            ORDER BY last_message_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "contact_id": contact_id},
    ).scalar_one_or_none()

    if conversation_id is not None:
        return conversation_id

    return connection.execute(
        text(
            """
            INSERT INTO conversations (tenant_id, contact_id, state)
            VALUES (:tenant_id, :contact_id, 'AUTOMATED')
            RETURNING id
            """
        ),
        {"tenant_id": tenant_id, "contact_id": contact_id},
    ).scalar_one()
