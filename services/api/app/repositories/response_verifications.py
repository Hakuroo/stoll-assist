import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True)
class StoredResponseVerification:
    verification_id: UUID
    plan_id: UUID
    conversation_id: UUID
    message_id: UUID
    status: str
    reason_code: str
    checks: dict[str, Any]
    unsupported_claims: list[str]
    verifier_version: str
    created_at: Any
    updated_at: Any


def upsert_response_verification(
    *,
    engine: Engine,
    tenant_slug: str,
    plan_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    status: str,
    reason_code: str,
    checks: dict[str, Any],
    unsupported_claims: list[str],
    verifier_version: str,
) -> StoredResponseVerification:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                INSERT INTO response_verifications (
                    tenant_id,
                    plan_id,
                    conversation_id,
                    message_id,
                    status,
                    reason_code,
                    checks,
                    unsupported_claims,
                    verifier_version
                )
                VALUES (
                    :tenant_id,
                    :plan_id,
                    :conversation_id,
                    :message_id,
                    :status,
                    :reason_code,
                    CAST(:checks AS jsonb),
                    CAST(:unsupported_claims AS jsonb),
                    :verifier_version
                )
                ON CONFLICT (tenant_id, plan_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    reason_code = EXCLUDED.reason_code,
                    checks = EXCLUDED.checks,
                    unsupported_claims = EXCLUDED.unsupported_claims,
                    verifier_version = EXCLUDED.verifier_version,
                    updated_at = now()
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "status": status,
                "reason_code": reason_code,
                "checks": json.dumps(checks, ensure_ascii=False),
                "unsupported_claims": json.dumps(unsupported_claims, ensure_ascii=False),
                "verifier_version": verifier_version,
            },
        ).mappings().one()

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
                    'RESPONSE_VERIFIED',
                    :status,
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "status": status,
                "payload": json.dumps(
                    {
                        "plan_id": str(plan_id),
                        "message_id": str(message_id),
                        "reason_code": reason_code,
                        "unsupported_claims": unsupported_claims,
                        "verifier_version": verifier_version,
                    },
                    ensure_ascii=False,
                ),
            },
        )
        return _row_to_verification(row)


def get_verification_by_provider_message_id(
    *, engine: Engine, tenant_slug: str, provider_message_id: str
) -> StoredResponseVerification | None:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                SELECT rv.*
                FROM response_verifications rv
                JOIN messages m ON m.id = rv.message_id
                WHERE rv.tenant_id = :tenant_id
                  AND m.provider_message_id = :provider_message_id
                LIMIT 1
                """
            ),
            {
                "tenant_id": tenant_id,
                "provider_message_id": provider_message_id,
            },
        ).mappings().one_or_none()
        return None if row is None else _row_to_verification(row)


def _row_to_verification(row: Any) -> StoredResponseVerification:
    return StoredResponseVerification(
        verification_id=row["id"],
        plan_id=row["plan_id"],
        conversation_id=row["conversation_id"],
        message_id=row["message_id"],
        status=row["status"],
        reason_code=row["reason_code"],
        checks=dict(row["checks"] or {}),
        unsupported_claims=list(row["unsupported_claims"] or []),
        verifier_version=row["verifier_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _get_active_tenant_id(connection: Connection, tenant_slug: str) -> UUID:
    tenant_id = connection.execute(
        text("SELECT id FROM tenants WHERE slug = :slug AND status = 'active'"),
        {"slug": tenant_slug},
    ).scalar_one_or_none()
    if tenant_id is None:
        raise LookupError(f"Active tenant not found: {tenant_slug}")
    return tenant_id
