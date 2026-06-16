import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text


class OutboxTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class StoredOutboundMessage:
    outbound_id: UUID
    conversation_id: UUID
    in_reply_to_message_id: UUID
    plan_id: UUID
    verification_id: UUID
    channel: str
    recipient: str
    display_name: str | None
    body_text: str
    body_sha256: str
    status: str
    requires_review: bool
    approved_by: str | None
    approved_at: Any
    rejected_by: str | None
    rejected_at: Any
    rejection_reason: str | None
    provider_message_id: str | None
    send_attempt_count: int
    last_attempt_at: Any
    lease_owner: str | None
    lease_expires_at: Any
    current_send_attempt_id: UUID | None
    sent_at: Any
    failed_at: Any
    unknown_at: Any
    delivery_status: str | None
    delivered_at: Any
    read_at: Any
    provider_failed_at: Any
    delivery_error_code: str | None
    delivery_error_message: str | None
    error_message: str | None
    created_at: Any
    updated_at: Any


@dataclass(frozen=True)
class OutboundDeliveryStatusResult:
    matched: bool
    duplicate: bool
    updated: bool
    status: str | None


def create_outbound_draft(
    *,
    engine: Engine,
    tenant_slug: str,
    verification_id: UUID,
) -> StoredOutboundMessage | None:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        source = connection.execute(
            text(
                """
                SELECT
                    t.outbound_mode,
                    c.id AS conversation_id,
                    c.state AS conversation_state,
                    ct.whatsapp_user_id AS recipient,
                    ct.display_name,
                    rp.id AS plan_id,
                    rp.message_id,
                    rp.decision,
                    rp.draft_reply,
                    rv.id AS verification_id,
                    rv.status AS verification_status
                FROM response_verifications rv
                JOIN response_plans rp ON rp.id = rv.plan_id
                JOIN conversations c ON c.id = rv.conversation_id
                JOIN contacts ct ON ct.id = c.contact_id
                JOIN tenants t ON t.id = rv.tenant_id
                WHERE rv.id = :verification_id
                  AND rv.tenant_id = :tenant_id
                FOR UPDATE OF rv, rp, c
                """
            ),
            {"verification_id": verification_id, "tenant_id": tenant_id},
        ).mappings().one_or_none()

        if source is None:
            raise LookupError(f"Verification not found: {verification_id}")
        if source["verification_status"] != "APPROVED":
            return None
        if source["decision"] not in {"ANSWER", "ASK"}:
            return None
        if source["conversation_state"] != "AUTOMATED":
            return None
        if source["outbound_mode"] == "DISABLED":
            return None

        body = (source["draft_reply"] or "").strip()
        if not body:
            return None

        body_sha256 = _hash_body(body)
        automatic = source["outbound_mode"] == "AUTO_LOW_RISK"
        status = "APPROVED" if automatic else "PENDING_REVIEW"
        requires_review = not automatic
        approved_by = "system:auto-low-risk" if automatic else None
        approved_at = datetime.now(UTC) if automatic else None

        row = connection.execute(
            text(
                """
                INSERT INTO outbound_messages (
                    tenant_id,
                    conversation_id,
                    in_reply_to_message_id,
                    plan_id,
                    verification_id,
                    channel,
                    recipient,
                    body_text,
                    body_sha256,
                    status,
                    requires_review,
                    approved_by,
                    approved_at
                )
                VALUES (
                    :tenant_id,
                    :conversation_id,
                    :message_id,
                    :plan_id,
                    :verification_id,
                    'whatsapp',
                    :recipient,
                    :body_text,
                    :body_sha256,
                    :status,
                    :requires_review,
                    :approved_by,
                    :approved_at
                )
                ON CONFLICT (tenant_id, verification_id) DO NOTHING
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": source["conversation_id"],
                "message_id": source["message_id"],
                "plan_id": source["plan_id"],
                "verification_id": source["verification_id"],
                "recipient": source["recipient"],
                "body_text": body,
                "body_sha256": body_sha256,
                "status": status,
                "requires_review": requires_review,
                "approved_by": approved_by,
                "approved_at": approved_at,
            },
        ).mappings().one_or_none()

        created = row is not None
        if row is None:
            row = connection.execute(
                text(
                    """
                    SELECT *
                    FROM outbound_messages
                    WHERE tenant_id = :tenant_id
                      AND verification_id = :verification_id
                    """
                ),
                {"tenant_id": tenant_id, "verification_id": verification_id},
            ).mappings().one()

        if created:
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
                        'OUTBOUND_DRAFT_CREATED',
                        :decision,
                        CAST(:payload AS jsonb)
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "conversation_id": row["conversation_id"],
                    "decision": row["status"],
                    "payload": json.dumps(
                        {
                            "outbound_id": str(row["id"]),
                            "plan_id": str(row["plan_id"]),
                            "verification_id": str(row["verification_id"]),
                            "requires_review": row["requires_review"],
                            "body_sha256": row["body_sha256"],
                        },
                        ensure_ascii=False,
                    ),
                },
            )

        return _row_to_outbound(row, display_name=source["display_name"])


def list_outbound_messages(
    *,
    engine: Engine,
    tenant_slug: str,
    status_filter: str | None = None,
    limit: int = 50,
) -> list[StoredOutboundMessage]:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        if status_filter is None:
            query = text(
                """
                SELECT om.*, ct.display_name
                FROM outbound_messages om
                JOIN conversations c ON c.id = om.conversation_id
                JOIN contacts ct ON ct.id = c.contact_id
                WHERE om.tenant_id = :tenant_id
                ORDER BY om.created_at DESC
                LIMIT :limit
                """
            )
            params = {"tenant_id": tenant_id, "limit": limit}
        else:
            query = text(
                """
                SELECT om.*, ct.display_name
                FROM outbound_messages om
                JOIN conversations c ON c.id = om.conversation_id
                JOIN contacts ct ON ct.id = c.contact_id
                WHERE om.tenant_id = :tenant_id
                  AND om.status = :status_filter
                ORDER BY om.created_at DESC
                LIMIT :limit
                """
            )
            params = {
                "tenant_id": tenant_id,
                "status_filter": status_filter,
                "limit": limit,
            }

        rows = connection.execute(query, params).mappings().all()
        return [_row_to_outbound(row) for row in rows]


def get_outbound_message(
    *, engine: Engine, tenant_slug: str, outbound_id: UUID
) -> StoredOutboundMessage:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = _load_outbound(connection, tenant_id, outbound_id)
        if row is None:
            raise LookupError(f"Outbound message not found: {outbound_id}")
        return _row_to_outbound(row)


def get_outbound_by_provider_message_id(
    *, engine: Engine, tenant_slug: str, provider_message_id: str
) -> StoredOutboundMessage | None:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                SELECT om.*, ct.display_name
                FROM outbound_messages om
                JOIN messages m ON m.id = om.in_reply_to_message_id
                JOIN conversations c ON c.id = om.conversation_id
                JOIN contacts ct ON ct.id = c.contact_id
                WHERE om.tenant_id = :tenant_id
                  AND m.provider_message_id = :provider_message_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "provider_message_id": provider_message_id},
        ).mappings().one_or_none()
        return None if row is None else _row_to_outbound(row)


def approve_outbound_message(
    *,
    engine: Engine,
    tenant_slug: str,
    outbound_id: UUID,
    operator_name: str,
) -> StoredOutboundMessage:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                SELECT
                    om.*,
                    c.state AS conversation_state,
                    rp.draft_reply AS current_draft_reply,
                    rv.status AS verification_status,
                    ct.display_name
                FROM outbound_messages om
                JOIN conversations c ON c.id = om.conversation_id
                JOIN contacts ct ON ct.id = c.contact_id
                JOIN response_plans rp ON rp.id = om.plan_id
                JOIN response_verifications rv ON rv.id = om.verification_id
                WHERE om.id = :outbound_id
                  AND om.tenant_id = :tenant_id
                FOR UPDATE OF om, c, rp, rv
                """
            ),
            {"outbound_id": outbound_id, "tenant_id": tenant_id},
        ).mappings().one_or_none()
        if row is None:
            raise LookupError(f"Outbound message not found: {outbound_id}")
        if row["status"] == "APPROVED":
            return _row_to_outbound(row)
        if row["status"] != "PENDING_REVIEW":
            raise OutboxTransitionError(
                f"Only PENDING_REVIEW messages can be approved; current status is {row['status']}"
            )
        if row["conversation_state"] != "AUTOMATED":
            raise OutboxTransitionError(
                "The conversation is no longer automated; the draft cannot be approved"
            )
        if row["verification_status"] != "APPROVED":
            raise OutboxTransitionError("The response verification is no longer approved")

        current_body = (row["current_draft_reply"] or "").strip()
        if not current_body or _hash_body(current_body) != row["body_sha256"]:
            raise OutboxTransitionError(
                "The response plan changed after verification; it must be verified again"
            )

        updated = connection.execute(
            text(
                """
                UPDATE outbound_messages
                SET status = 'APPROVED',
                    approved_by = :operator_name,
                    approved_at = now(),
                    updated_at = now()
                WHERE id = :outbound_id
                RETURNING *
                """
            ),
            {"operator_name": operator_name, "outbound_id": outbound_id},
        ).mappings().one()
        _audit_transition(
            connection=connection,
            tenant_id=tenant_id,
            conversation_id=updated["conversation_id"],
            outbound_id=outbound_id,
            event_type="OUTBOUND_APPROVED",
            decision="APPROVED",
            actor=operator_name,
            note=None,
        )
        return _row_to_outbound(updated, display_name=row["display_name"])


def reject_outbound_message(
    *,
    engine: Engine,
    tenant_slug: str,
    outbound_id: UUID,
    operator_name: str,
    reason: str,
) -> StoredOutboundMessage:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = _load_outbound(connection, tenant_id, outbound_id, for_update=True)
        if row is None:
            raise LookupError(f"Outbound message not found: {outbound_id}")
        if row["status"] == "REJECTED":
            return _row_to_outbound(row)
        if row["status"] != "PENDING_REVIEW":
            raise OutboxTransitionError(
                f"Only PENDING_REVIEW messages can be rejected; current status is {row['status']}"
            )

        updated = connection.execute(
            text(
                """
                UPDATE outbound_messages
                SET status = 'REJECTED',
                    rejected_by = :operator_name,
                    rejected_at = now(),
                    rejection_reason = :reason,
                    updated_at = now()
                WHERE id = :outbound_id
                RETURNING *
                """
            ),
            {
                "operator_name": operator_name,
                "reason": reason,
                "outbound_id": outbound_id,
            },
        ).mappings().one()
        _audit_transition(
            connection=connection,
            tenant_id=tenant_id,
            conversation_id=updated["conversation_id"],
            outbound_id=outbound_id,
            event_type="OUTBOUND_REJECTED",
            decision="REJECTED",
            actor=operator_name,
            note=reason,
        )
        return _row_to_outbound(updated, display_name=row["display_name"])


def claim_outbound_for_send(
    *,
    engine: Engine,
    tenant_slug: str,
    outbound_id: UUID,
    operator_name: str,
    lease_owner: str | None = None,
    lease_seconds: int = 120,
) -> StoredOutboundMessage:
    lease_owner = lease_owner or operator_name
    lease_seconds = max(1, lease_seconds)
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = _load_outbound(connection, tenant_id, outbound_id, for_update=True)
        if row is None:
            raise LookupError(f"Outbound message not found: {outbound_id}")
        if row["status"] == "SENT" and row["provider_message_id"]:
            return _row_to_outbound(row)
        if row["status"] == "UNKNOWN":
            raise OutboxTransitionError(
                "Outbound message has an unknown send result and requires manual review"
            )
        if row["status"] == "QUEUED":
            lease_expires_at = row["lease_expires_at"]
            if lease_expires_at is not None and lease_expires_at > datetime.now(UTC):
                raise OutboxTransitionError("Outbound message is already being sent")
        elif row["status"] != "APPROVED":
            raise OutboxTransitionError(
                f"Only APPROVED messages can be sent; current status is {row['status']}"
            )
        if row["provider_message_id"] or row["sent_at"] is not None:
            raise OutboxTransitionError("Outbound message already has provider send metadata")

        attempt_number = int(row["send_attempt_count"]) + 1
        attempt_id = connection.execute(
            text(
                """
                INSERT INTO outbound_send_attempts (
                    tenant_id,
                    outbound_message_id,
                    attempt_number,
                    lease_owner,
                    status
                )
                VALUES (
                    :tenant_id,
                    :outbound_id,
                    :attempt_number,
                    :lease_owner,
                    'CLAIMED'
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "outbound_id": outbound_id,
                "attempt_number": attempt_number,
                "lease_owner": lease_owner,
            },
        ).scalar_one()

        updated = connection.execute(
            text(
                """
                UPDATE outbound_messages
                SET status = 'QUEUED',
                    send_attempt_count = :attempt_number,
                    last_attempt_at = now(),
                    lease_owner = :lease_owner,
                    lease_expires_at = now() + (:lease_seconds * interval '1 second'),
                    current_send_attempt_id = :attempt_id,
                    failed_at = NULL,
                    unknown_at = NULL,
                    error_message = NULL,
                    updated_at = now()
                WHERE id = :outbound_id
                RETURNING *
                """
            ),
            {
                "outbound_id": outbound_id,
                "attempt_number": attempt_number,
                "lease_owner": lease_owner,
                "lease_seconds": lease_seconds,
                "attempt_id": attempt_id,
            },
        ).mappings().one()
        _audit_transition(
            connection=connection,
            tenant_id=tenant_id,
            conversation_id=updated["conversation_id"],
            outbound_id=outbound_id,
            event_type="OUTBOUND_SEND_LEASE_CLAIMED",
            decision="QUEUED",
            actor=operator_name,
            note=None,
        )
        return _row_to_outbound(updated, display_name=row["display_name"])


def mark_outbound_sent(
    *,
    engine: Engine,
    tenant_slug: str,
    outbound_id: UUID,
    attempt_id: UUID,
    lease_owner: str,
    provider_message_id: str,
    latency_ms: int,
    operator_name: str,
) -> StoredOutboundMessage:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                UPDATE outbound_messages
                SET status = 'SENT',
                    provider_message_id = :provider_message_id,
                    sent_at = COALESCE(sent_at, now()),
                    delivery_status = COALESCE(delivery_status, 'SENT'),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    current_send_attempt_id = NULL,
                    failed_at = NULL,
                    unknown_at = NULL,
                    error_message = NULL,
                    updated_at = now()
                WHERE id = :outbound_id
                  AND tenant_id = :tenant_id
                  AND status = 'QUEUED'
                  AND current_send_attempt_id = :attempt_id
                  AND lease_owner = :lease_owner
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "outbound_id": outbound_id,
                "attempt_id": attempt_id,
                "lease_owner": lease_owner,
                "provider_message_id": provider_message_id,
            },
        ).mappings().one_or_none()
        if row is None:
            raise OutboxTransitionError("Outbound message could not be marked as sent")
        _complete_send_attempt(
            connection=connection,
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            status="SENT",
            provider_message_id=provider_message_id,
            request_started=True,
            latency_ms=latency_ms,
            error_type=None,
            error_message=None,
        )
        _audit_transition(
            connection=connection,
            tenant_id=tenant_id,
            conversation_id=row["conversation_id"],
            outbound_id=outbound_id,
            event_type="OUTBOUND_SENT",
            decision="SENT",
            actor=operator_name,
            note=None,
        )
        return _row_to_outbound(row)


def mark_outbound_send_failed(
    *,
    engine: Engine,
    tenant_slug: str,
    outbound_id: UUID,
    attempt_id: UUID,
    lease_owner: str,
    error_message: str,
    error_type: str,
    request_started: bool,
    latency_ms: int | None,
    provider_message_id: str | None,
    operator_name: str,
) -> StoredOutboundMessage:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                UPDATE outbound_messages
                SET status = 'FAILED',
                    provider_message_id = COALESCE(provider_message_id, :provider_message_id),
                    failed_at = now(),
                    error_message = :error_message,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    current_send_attempt_id = NULL,
                    updated_at = now()
                WHERE id = :outbound_id
                  AND tenant_id = :tenant_id
                  AND status = 'QUEUED'
                  AND current_send_attempt_id = :attempt_id
                  AND lease_owner = :lease_owner
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "outbound_id": outbound_id,
                "error_message": error_message[:1000],
                "provider_message_id": provider_message_id,
                "attempt_id": attempt_id,
                "lease_owner": lease_owner,
            },
        ).mappings().one_or_none()
        if row is None:
            raise OutboxTransitionError("Outbound message could not be marked as failed")
        _complete_send_attempt(
            connection=connection,
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            status="FAILED",
            provider_message_id=provider_message_id,
            request_started=request_started,
            latency_ms=latency_ms,
            error_type=error_type,
            error_message=error_message,
        )
        _audit_transition(
            connection=connection,
            tenant_id=tenant_id,
            conversation_id=row["conversation_id"],
            outbound_id=outbound_id,
            event_type="OUTBOUND_SEND_FAILED",
            decision="FAILED",
            actor=operator_name,
            note=error_message[:500],
        )
        return _row_to_outbound(row)


def mark_outbound_send_unknown(
    *,
    engine: Engine,
    tenant_slug: str,
    outbound_id: UUID,
    attempt_id: UUID,
    lease_owner: str,
    error_message: str,
    error_type: str,
    request_started: bool,
    latency_ms: int | None,
    provider_message_id: str | None,
    operator_name: str,
) -> StoredOutboundMessage:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                UPDATE outbound_messages
                SET status = 'UNKNOWN',
                    provider_message_id = COALESCE(provider_message_id, :provider_message_id),
                    unknown_at = now(),
                    error_message = :error_message,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    current_send_attempt_id = NULL,
                    updated_at = now()
                WHERE id = :outbound_id
                  AND tenant_id = :tenant_id
                  AND status = 'QUEUED'
                  AND current_send_attempt_id = :attempt_id
                  AND lease_owner = :lease_owner
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "outbound_id": outbound_id,
                "attempt_id": attempt_id,
                "lease_owner": lease_owner,
                "provider_message_id": provider_message_id,
                "error_message": error_message[:1000],
            },
        ).mappings().one_or_none()
        if row is None:
            raise OutboxTransitionError("Outbound message could not be marked as unknown")
        _complete_send_attempt(
            connection=connection,
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            status="UNKNOWN",
            provider_message_id=provider_message_id,
            request_started=request_started,
            latency_ms=latency_ms,
            error_type=error_type,
            error_message=error_message,
        )
        _audit_transition(
            connection=connection,
            tenant_id=tenant_id,
            conversation_id=row["conversation_id"],
            outbound_id=outbound_id,
            event_type="OUTBOUND_SEND_UNKNOWN",
            decision="UNKNOWN",
            actor=operator_name,
            note=error_message[:500],
        )
        return _row_to_outbound(row)


def record_outbound_delivery_status(
    *,
    engine: Engine,
    tenant_slug: str,
    provider_message_id: str,
    delivery_status: str,
    provider_timestamp: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> OutboundDeliveryStatusResult:
    normalized_status = _normalize_delivery_status(delivery_status)
    dedupe_key = _delivery_dedupe_key(
        provider_message_id=provider_message_id,
        delivery_status=normalized_status,
        error_code=error_code,
    )

    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                SELECT id, conversation_id, delivery_status
                FROM outbound_messages
                WHERE tenant_id = :tenant_id
                  AND provider_message_id = :provider_message_id
                FOR UPDATE
                """
            ),
            {"tenant_id": tenant_id, "provider_message_id": provider_message_id},
        ).mappings().one_or_none()
        if row is None:
            return OutboundDeliveryStatusResult(
                matched=False,
                duplicate=False,
                updated=False,
                status=None,
            )

        inserted = connection.execute(
            text(
                """
                INSERT INTO outbound_delivery_events (
                    tenant_id,
                    outbound_message_id,
                    provider_message_id,
                    delivery_status,
                    provider_timestamp,
                    error_code,
                    error_message,
                    dedupe_key
                )
                VALUES (
                    :tenant_id,
                    :outbound_id,
                    :provider_message_id,
                    :delivery_status,
                    :provider_timestamp,
                    :error_code,
                    :error_message,
                    :dedupe_key
                )
                ON CONFLICT (tenant_id, dedupe_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "outbound_id": row["id"],
                "provider_message_id": provider_message_id,
                "delivery_status": normalized_status,
                "provider_timestamp": provider_timestamp,
                "error_code": error_code,
                "error_message": None if error_message is None else error_message[:1000],
                "dedupe_key": dedupe_key,
            },
        ).scalar_one_or_none()
        if inserted is None:
            return OutboundDeliveryStatusResult(
                matched=True,
                duplicate=True,
                updated=False,
                status=row["delivery_status"],
            )

        current_status = row["delivery_status"]
        if _delivery_rank(normalized_status) <= _delivery_rank(current_status):
            return OutboundDeliveryStatusResult(
                matched=True,
                duplicate=False,
                updated=False,
                status=current_status,
            )

        updated_status = connection.execute(
            text(
                """
                UPDATE outbound_messages
                SET delivery_status = :delivery_status,
                    sent_at = CASE
                        WHEN :delivery_status = 'SENT' THEN COALESCE(sent_at, :provider_timestamp, now())
                        ELSE sent_at
                    END,
                    delivered_at = CASE
                        WHEN :delivery_status = 'DELIVERED' THEN COALESCE(delivered_at, :provider_timestamp, now())
                        ELSE delivered_at
                    END,
                    read_at = CASE
                        WHEN :delivery_status = 'READ' THEN COALESCE(read_at, :provider_timestamp, now())
                        ELSE read_at
                    END,
                    provider_failed_at = CASE
                        WHEN :delivery_status = 'FAILED' THEN COALESCE(provider_failed_at, :provider_timestamp, now())
                        ELSE provider_failed_at
                    END,
                    delivery_error_code = CASE
                        WHEN :delivery_status = 'FAILED' THEN :error_code
                        ELSE delivery_error_code
                    END,
                    delivery_error_message = CASE
                        WHEN :delivery_status = 'FAILED' THEN :error_message
                        ELSE delivery_error_message
                    END,
                    updated_at = now()
                WHERE id = :outbound_id
                  AND tenant_id = :tenant_id
                RETURNING delivery_status
                """
            ),
            {
                "tenant_id": tenant_id,
                "outbound_id": row["id"],
                "delivery_status": normalized_status,
                "provider_timestamp": provider_timestamp,
                "error_code": error_code,
                "error_message": None if error_message is None else error_message[:1000],
            },
        ).scalar_one()

        return OutboundDeliveryStatusResult(
            matched=True,
            duplicate=False,
            updated=True,
            status=updated_status,
        )


def _complete_send_attempt(
    *,
    connection: Connection,
    tenant_id: UUID,
    attempt_id: UUID,
    status: str,
    provider_message_id: str | None,
    request_started: bool,
    latency_ms: int | None,
    error_type: str | None,
    error_message: str | None,
) -> None:
    connection.execute(
        text(
            """
            UPDATE outbound_send_attempts
            SET status = :status,
                provider_message_id = :provider_message_id,
                request_started = :request_started,
                latency_ms = :latency_ms,
                error_type = :error_type,
                error_message = :error_message,
                completed_at = now(),
                updated_at = now()
            WHERE id = :attempt_id
              AND tenant_id = :tenant_id
            """
        ),
        {
            "tenant_id": tenant_id,
            "attempt_id": attempt_id,
            "status": status,
            "provider_message_id": provider_message_id,
            "request_started": request_started,
            "latency_ms": latency_ms,
            "error_type": error_type,
            "error_message": None if error_message is None else error_message[:1000],
        },
    )


def _normalize_delivery_status(value: str) -> str:
    status = value.strip().upper()
    if status == "DELIVERED":
        return "DELIVERED"
    if status == "READ":
        return "READ"
    if status == "FAILED":
        return "FAILED"
    if status == "SENT":
        return "SENT"
    raise OutboxTransitionError(f"Unsupported WhatsApp delivery status: {value}")


def _delivery_rank(value: str | None) -> int:
    return {
        None: 0,
        "SENT": 1,
        "FAILED": 2,
        "DELIVERED": 3,
        "READ": 4,
    }.get(value, 0)


def _delivery_dedupe_key(
    *, provider_message_id: str, delivery_status: str, error_code: str | None
) -> str:
    raw = "|".join(
        [
            provider_message_id,
            delivery_status,
            error_code or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_outbound(
    connection: Connection,
    tenant_id: UUID,
    outbound_id: UUID,
    *,
    for_update: bool = False,
) -> Any:
    suffix = " FOR UPDATE OF om" if for_update else ""
    return connection.execute(
        text(
            """
            SELECT om.*, ct.display_name
            FROM outbound_messages om
            JOIN conversations c ON c.id = om.conversation_id
            JOIN contacts ct ON ct.id = c.contact_id
            WHERE om.id = :outbound_id
              AND om.tenant_id = :tenant_id
            """ + suffix
        ),
        {"outbound_id": outbound_id, "tenant_id": tenant_id},
    ).mappings().one_or_none()


def _audit_transition(
    *,
    connection: Connection,
    tenant_id: UUID,
    conversation_id: UUID,
    outbound_id: UUID,
    event_type: str,
    decision: str,
    actor: str,
    note: str | None,
) -> None:
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
                :event_type,
                :decision,
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "event_type": event_type,
            "decision": decision,
            "payload": json.dumps(
                {
                    "outbound_id": str(outbound_id),
                    "actor": actor,
                    "note": note,
                },
                ensure_ascii=False,
            ),
        },
    )


def _row_to_outbound(
    row: Any, *, display_name: str | None = None
) -> StoredOutboundMessage:
    return StoredOutboundMessage(
        outbound_id=row["id"],
        conversation_id=row["conversation_id"],
        in_reply_to_message_id=row["in_reply_to_message_id"],
        plan_id=row["plan_id"],
        verification_id=row["verification_id"],
        channel=row["channel"],
        recipient=row["recipient"],
        display_name=display_name if display_name is not None else row.get("display_name"),
        body_text=row["body_text"],
        body_sha256=row["body_sha256"],
        status=row["status"],
        requires_review=row["requires_review"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        rejected_by=row["rejected_by"],
        rejected_at=row["rejected_at"],
        rejection_reason=row["rejection_reason"],
        provider_message_id=row["provider_message_id"],
        send_attempt_count=row["send_attempt_count"],
        last_attempt_at=row["last_attempt_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        current_send_attempt_id=row["current_send_attempt_id"],
        sent_at=row["sent_at"],
        failed_at=row["failed_at"],
        unknown_at=row["unknown_at"],
        delivery_status=row["delivery_status"],
        delivered_at=row["delivered_at"],
        read_at=row["read_at"],
        provider_failed_at=row["provider_failed_at"],
        delivery_error_code=row["delivery_error_code"],
        delivery_error_message=row["delivery_error_message"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _get_active_tenant_id(connection: Connection, tenant_slug: str) -> UUID:
    tenant_id = connection.execute(
        text("SELECT id FROM tenants WHERE slug = :slug AND status = 'active'"),
        {"slug": tenant_slug},
    ).scalar_one_or_none()
    if tenant_id is None:
        raise LookupError(f"Active tenant not found: {tenant_slug}")
    return tenant_id
