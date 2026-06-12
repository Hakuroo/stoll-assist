import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class StoredWebhookEvent:
    event_id: UUID
    duplicate: bool
    status: str


@dataclass(frozen=True)
class WebhookClaim:
    claimed: bool
    status: str


@dataclass(frozen=True)
class WebhookForProcessing:
    event_id: UUID
    tenant_slug: str
    payload: dict[str, Any]
    attempt_count: int


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

        inserted = connection.execute(
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
                RETURNING id, status
                """
            ),
            {
                "tenant_id": tenant_id,
                "provider": provider,
                "provider_event_id": provider_event_id,
                "event_kind": event_kind,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        ).mappings().one_or_none()

        if inserted is not None:
            return StoredWebhookEvent(
                event_id=inserted["id"],
                duplicate=False,
                status=inserted["status"],
            )

        existing = connection.execute(
            text(
                """
                SELECT id, status
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
        ).mappings().one()

        return StoredWebhookEvent(
            event_id=existing["id"],
            duplicate=True,
            status=existing["status"],
        )


def mark_webhook_queued(*, engine: Engine, event_id: UUID) -> WebhookClaim:
    with engine.begin() as connection:
        queued_status = connection.execute(
            text(
                """
                UPDATE webhook_events
                SET status = 'QUEUED',
                    queued_at = now(),
                    processed_at = NULL,
                    error_message = NULL
                WHERE id = :event_id
                  AND status IN ('RECEIVED', 'FAILED')
                RETURNING status
                """
            ),
            {"event_id": event_id},
        ).scalar_one_or_none()

        if queued_status is not None:
            return WebhookClaim(claimed=True, status=queued_status)

        current_status = connection.execute(
            text("SELECT status FROM webhook_events WHERE id = :event_id"),
            {"event_id": event_id},
        ).scalar_one()
        return WebhookClaim(claimed=False, status=current_status)


def claim_webhook_event(*, engine: Engine, event_id: UUID) -> WebhookClaim:
    with engine.begin() as connection:
        claimed_status = connection.execute(
            text(
                """
                UPDATE webhook_events
                SET status = 'PROCESSING',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = now(),
                    error_message = NULL
                WHERE id = :event_id
                  AND status = 'QUEUED'
                RETURNING status
                """
            ),
            {"event_id": event_id},
        ).scalar_one_or_none()

        if claimed_status is not None:
            return WebhookClaim(claimed=True, status=claimed_status)

        current_status = connection.execute(
            text("SELECT status FROM webhook_events WHERE id = :event_id"),
            {"event_id": event_id},
        ).scalar_one()
        return WebhookClaim(claimed=False, status=current_status)


def get_webhook_event_for_processing(
    *, engine: Engine, event_id: UUID
) -> WebhookForProcessing:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT we.id, t.slug AS tenant_slug, we.payload, we.attempt_count
                FROM webhook_events we
                JOIN tenants t ON t.id = we.tenant_id
                WHERE we.id = :event_id
                """
            ),
            {"event_id": event_id},
        ).mappings().one_or_none()

    if row is None:
        raise LookupError(f"Webhook event not found: {event_id}")

    payload = row["payload"]
    if not isinstance(payload, dict):
        raise TypeError(f"Webhook payload is not a JSON object: {event_id}")

    return WebhookForProcessing(
        event_id=row["id"],
        tenant_slug=row["tenant_slug"],
        payload=payload,
        attempt_count=row["attempt_count"],
    )


def requeue_failed_webhook_event(*, engine: Engine, event_id: UUID) -> bool:
    with engine.begin() as connection:
        return (
            connection.execute(
                text(
                    """
                    UPDATE webhook_events
                    SET status = 'QUEUED',
                        queued_at = now(),
                        processed_at = NULL
                    WHERE id = :event_id
                      AND status = 'FAILED'
                    RETURNING id
                    """
                ),
                {"event_id": event_id},
            ).scalar_one_or_none()
            is not None
        )


def complete_webhook_event(
    *,
    engine: Engine,
    event_id: UUID,
    status: str,
    error_message: str | None = None,
) -> None:
    if status not in {"PROCESSED", "IGNORED", "FAILED"}:
        raise ValueError(f"Unsupported terminal webhook status: {status}")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE webhook_events
                SET status = :status,
                    processed_at = now(),
                    error_message = :error_message
                WHERE id = :event_id
                """
            ),
            {
                "event_id": event_id,
                "status": status,
                "error_message": error_message,
            },
        )
