import json
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from app.normalization import NormalizedInboundMessage


def persist_inbound_messages(
    *,
    engine: Engine,
    tenant_slug: str,
    messages: Sequence[NormalizedInboundMessage],
) -> int:
    if not messages:
        return 0

    inserted_count = 0

    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)

        for message in messages:
            contact_id = _upsert_contact(connection, tenant_id, message)
            conversation_id = _get_or_create_active_conversation(
                connection,
                tenant_id,
                contact_id,
            )

            inserted_id = connection.execute(
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
                    RETURNING id
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
            ).scalar_one_or_none()

            if inserted_id is None:
                continue

            inserted_count += 1
            effective_timestamp = message.provider_timestamp
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
                    "message_timestamp": effective_timestamp,
                },
            )

    return inserted_count


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
    # Serializa la creación de la conversación activa para este contacto.
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
