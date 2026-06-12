import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True)
class StoredResponseGeneration:
    generation_id: UUID
    conversation_id: UUID
    message_id: UUID
    response_plan_id: UUID
    provider: str
    model: str
    prompt_version: str
    status: str
    provider_request_id: str | None
    input_hash: str
    structured_output: dict[str, Any] | None
    latency_ms: int | None
    token_usage: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: Any | None
    started_at: Any | None
    completed_at: Any | None
    created_at: Any
    updated_at: Any


@dataclass(frozen=True)
class ResponseGenerationClaim:
    generation: StoredResponseGeneration
    claimed: bool


def get_response_generation_by_plan_id(
    *,
    engine: Engine,
    tenant_slug: str,
    response_plan_id: UUID,
) -> StoredResponseGeneration | None:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                SELECT *
                FROM response_generations
                WHERE tenant_id = :tenant_id
                  AND response_plan_id = :response_plan_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "response_plan_id": response_plan_id,
            },
        ).mappings().one_or_none()
        return None if row is None else _row_to_generation(row)


def claim_response_generation(
    *,
    engine: Engine,
    tenant_slug: str,
    conversation_id: UUID,
    message_id: UUID,
    response_plan_id: UUID,
    provider: str,
    model: str,
    prompt_version: str,
    input_hash: str,
    lease_owner: str,
    lease_seconds: int,
) -> ResponseGenerationClaim:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        params = {
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "response_plan_id": response_plan_id,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "input_hash": input_hash,
            "lease_owner": lease_owner,
            "lease_seconds": lease_seconds,
        }
        row = connection.execute(
            text(
                """
                INSERT INTO response_generations (
                    tenant_id,
                    conversation_id,
                    message_id,
                    response_plan_id,
                    provider,
                    model,
                    prompt_version,
                    status,
                    input_hash,
                    attempt_count,
                    lease_owner,
                    lease_expires_at,
                    started_at,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :conversation_id,
                    :message_id,
                    :response_plan_id,
                    :provider,
                    :model,
                    :prompt_version,
                    'IN_PROGRESS',
                    :input_hash,
                    1,
                    :lease_owner,
                    now() + (CAST(:lease_seconds AS integer) * interval '1 second'),
                    now(),
                    now()
                )
                ON CONFLICT (tenant_id, response_plan_id) DO NOTHING
                RETURNING *
                """
            ),
            params,
        ).mappings().one_or_none()
        if row is not None:
            return ResponseGenerationClaim(generation=_row_to_generation(row), claimed=True)

        row = connection.execute(
            text(
                """
                UPDATE response_generations
                SET provider = :provider,
                    model = :model,
                    prompt_version = :prompt_version,
                    status = 'IN_PROGRESS',
                    provider_request_id = NULL,
                    input_hash = :input_hash,
                    structured_output = NULL,
                    latency_ms = NULL,
                    token_usage = NULL,
                    error_code = NULL,
                    error_message = NULL,
                    attempt_count = attempt_count + 1,
                    lease_owner = :lease_owner,
                    lease_expires_at = now() + (CAST(:lease_seconds AS integer) * interval '1 second'),
                    started_at = now(),
                    completed_at = NULL,
                    updated_at = now()
                WHERE tenant_id = :tenant_id
                  AND response_plan_id = :response_plan_id
                  AND status = 'IN_PROGRESS'
                  AND lease_expires_at <= now()
                RETURNING *
                """
            ),
            params,
        ).mappings().one_or_none()
        if row is not None:
            return ResponseGenerationClaim(generation=_row_to_generation(row), claimed=True)

        row = connection.execute(
            text(
                """
                SELECT *
                FROM response_generations
                WHERE tenant_id = :tenant_id
                  AND response_plan_id = :response_plan_id
                """
            ),
            params,
        ).mappings().one()
        return ResponseGenerationClaim(generation=_row_to_generation(row), claimed=False)


def finish_response_generation_claim(
    *,
    engine: Engine,
    tenant_slug: str,
    generation_id: UUID,
    lease_owner: str,
    status: str,
    provider_request_id: str | None = None,
    structured_output: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    token_usage: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> StoredResponseGeneration:
    if status not in {"COMPLETED", "HANDOFF", "FAILED"}:
        raise ValueError(f"Unsupported terminal generation status: {status}")

    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                UPDATE response_generations
                SET status = :status,
                    provider_request_id = :provider_request_id,
                    structured_output = CAST(:structured_output AS jsonb),
                    latency_ms = :latency_ms,
                    token_usage = CAST(:token_usage AS jsonb),
                    error_code = :error_code,
                    error_message = :error_message,
                    lease_expires_at = NULL,
                    completed_at = now(),
                    updated_at = now()
                WHERE tenant_id = :tenant_id
                  AND id = :generation_id
                  AND status = 'IN_PROGRESS'
                  AND lease_owner = :lease_owner
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "generation_id": generation_id,
                "lease_owner": lease_owner,
                "status": status,
                "provider_request_id": provider_request_id,
                "structured_output": _to_json(structured_output),
                "latency_ms": latency_ms,
                "token_usage": _to_json(token_usage),
                "error_code": error_code,
                "error_message": None if error_message is None else error_message[:2000],
            },
        ).mappings().one_or_none()
        if row is not None:
            return _row_to_generation(row)

        row = connection.execute(
            text(
                """
                SELECT *
                FROM response_generations
                WHERE tenant_id = :tenant_id
                  AND id = :generation_id
                """
            ),
            {"tenant_id": tenant_id, "generation_id": generation_id},
        ).mappings().one()
        return _row_to_generation(row)


def count_generations_for_message(
    *, engine: Engine, tenant_slug: str, provider_message_id: str
) -> int:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        return int(
            connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM response_generations rg
                    JOIN messages m ON m.id = rg.message_id
                    WHERE rg.tenant_id = :tenant_id
                      AND m.provider_message_id = :provider_message_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "provider_message_id": provider_message_id,
                },
            ).scalar_one()
        )


def _row_to_generation(row: Any) -> StoredResponseGeneration:
    return StoredResponseGeneration(
        generation_id=row["id"],
        conversation_id=row["conversation_id"],
        message_id=row["message_id"],
        response_plan_id=row["response_plan_id"],
        provider=row["provider"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        status=row["status"],
        provider_request_id=row["provider_request_id"],
        input_hash=row["input_hash"],
        structured_output=(
            None if row["structured_output"] is None else dict(row["structured_output"])
        ),
        latency_ms=row["latency_ms"],
        token_usage=None if row["token_usage"] is None else dict(row["token_usage"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        attempt_count=row["attempt_count"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_json(value: dict[str, Any] | None) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _get_active_tenant_id(connection: Connection, tenant_slug: str) -> UUID:
    tenant_id = connection.execute(
        text("SELECT id FROM tenants WHERE slug = :slug AND status = 'active'"),
        {"slug": tenant_slug},
    ).scalar_one_or_none()
    if tenant_id is None:
        raise LookupError(f"Active tenant not found: {tenant_slug}")
    return tenant_id
