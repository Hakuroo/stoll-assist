from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class StoredWebhookEvent:
    event_id: UUID
    duplicate: bool


def store_webhook_event(
    *,
    engine: Engine,
    tenant_slug: str,
    provider: str,
    provider_event_id: str,
    event_kind: str,
    payload: dict[str, Any],
) -> StoredWebhookEvent:
    with engine.begin() as connection:
        tenant_id = connection.execute(
            text("SELECT id FROM tenants WHERE slug = :slug AND status = 'active'"),
            {"slug": tenant_slug},
        ).scalar_one_or_none()

        if tenant_id is None:
            raise LookupError(f"Active tenant not found: {tenant_slug}")

        inserted_id = connection.execute(
            text(
                """
                INSERT INTO webhook_events (
                    tenant_id,
                    provider,
                    provider_event_id,
                    event_kind,
                    signature_valid,
                    status,
                    payload
                )
                VALUES (
                    :tenant_id,
                    :provider,
                    :provider_event_id,
                    :event_kind,
                    true,
                    'RECEIVED',
                    CAST(:payload AS jsonb)
                )
                ON CONFLICT (tenant_id, provider, provider_event_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "provider": provider,
                "provider_event_id": provider_event_id,
                "event_kind": event_kind,
                "payload": __import__("json").dumps(payload, ensure_ascii=False),
            },
        ).scalar_one_or_none()

        if inserted_id is not None:
            return StoredWebhookEvent(event_id=inserted_id, duplicate=False)

        existing_id = connection.execute(
            text(
                """
                SELECT id
                FROM webhook_events
                WHERE tenant_id = :tenant_id
                  AND provider = :provider
                  AND provider_event_id = :provider_event_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "provider": provider,
                "provider_event_id": provider_event_id,
            },
        ).scalar_one()

        return StoredWebhookEvent(event_id=existing_id, duplicate=True)
