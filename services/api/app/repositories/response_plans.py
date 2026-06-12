import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True)
class StoredResponsePlan:
    plan_id: UUID
    conversation_id: UUID
    message_id: UUID
    decision: str
    reason_code: str
    risk_level: str
    policy_rule_key: str | None
    knowledge_item_ids: list[str]
    knowledge_keys: list[str]
    allowed_claims: list[str]
    forbidden_claims: list[str]
    reply_goal: str
    draft_reply: str | None
    planner_version: str
    created_at: Any
    updated_at: Any


def upsert_response_plan(
    *,
    engine: Engine,
    tenant_slug: str,
    conversation_id: UUID,
    message_id: UUID,
    decision: str,
    reason_code: str,
    risk_level: str,
    policy_rule_key: str | None,
    knowledge_item_ids: list[str],
    knowledge_keys: list[str],
    allowed_claims: list[str],
    forbidden_claims: list[str],
    reply_goal: str,
    draft_reply: str | None,
    planner_version: str = "0.8.0",
) -> StoredResponsePlan:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                INSERT INTO response_plans (
                    tenant_id,
                    conversation_id,
                    message_id,
                    decision,
                    reason_code,
                    risk_level,
                    policy_rule_key,
                    knowledge_item_ids,
                    knowledge_keys,
                    allowed_claims,
                    forbidden_claims,
                    reply_goal,
                    draft_reply,
                    planner_version
                )
                VALUES (
                    :tenant_id,
                    :conversation_id,
                    :message_id,
                    :decision,
                    :reason_code,
                    :risk_level,
                    :policy_rule_key,
                    CAST(:knowledge_item_ids AS jsonb),
                    CAST(:knowledge_keys AS jsonb),
                    CAST(:allowed_claims AS jsonb),
                    CAST(:forbidden_claims AS jsonb),
                    :reply_goal,
                    :draft_reply,
                    :planner_version
                )
                ON CONFLICT (tenant_id, message_id)
                DO UPDATE SET
                    decision = EXCLUDED.decision,
                    reason_code = EXCLUDED.reason_code,
                    risk_level = EXCLUDED.risk_level,
                    policy_rule_key = EXCLUDED.policy_rule_key,
                    knowledge_item_ids = EXCLUDED.knowledge_item_ids,
                    knowledge_keys = EXCLUDED.knowledge_keys,
                    allowed_claims = EXCLUDED.allowed_claims,
                    forbidden_claims = EXCLUDED.forbidden_claims,
                    reply_goal = EXCLUDED.reply_goal,
                    draft_reply = EXCLUDED.draft_reply,
                    planner_version = EXCLUDED.planner_version,
                    updated_at = now()
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "decision": decision,
                "reason_code": reason_code,
                "risk_level": risk_level,
                "policy_rule_key": policy_rule_key,
                "knowledge_item_ids": json.dumps(knowledge_item_ids),
                "knowledge_keys": json.dumps(knowledge_keys, ensure_ascii=False),
                "allowed_claims": json.dumps(allowed_claims, ensure_ascii=False),
                "forbidden_claims": json.dumps(forbidden_claims, ensure_ascii=False),
                "reply_goal": reply_goal,
                "draft_reply": draft_reply,
                "planner_version": planner_version,
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
                    'RESPONSE_PLAN_CREATED',
                    :decision,
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "decision": decision,
                "payload": json.dumps(
                    {
                        "message_id": str(message_id),
                        "reason_code": reason_code,
                        "risk_level": risk_level,
                        "policy_rule_key": policy_rule_key,
                        "knowledge_keys": knowledge_keys,
                        "planner_version": planner_version,
                    },
                    ensure_ascii=False,
                ),
            },
        )

        return _row_to_plan(row)


def get_plan_by_provider_message_id(
    *, engine: Engine, tenant_slug: str, provider_message_id: str
) -> StoredResponsePlan | None:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                SELECT rp.*
                FROM response_plans rp
                JOIN messages m ON m.id = rp.message_id
                WHERE rp.tenant_id = :tenant_id
                  AND m.provider_message_id = :provider_message_id
                LIMIT 1
                """
            ),
            {
                "tenant_id": tenant_id,
                "provider_message_id": provider_message_id,
            },
        ).mappings().one_or_none()
        return None if row is None else _row_to_plan(row)


def update_response_plan_draft(
    *,
    engine: Engine,
    tenant_slug: str,
    plan_id: UUID,
    draft_reply: str | None,
) -> StoredResponsePlan:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        row = connection.execute(
            text(
                """
                UPDATE response_plans
                SET draft_reply = :draft_reply,
                    updated_at = now()
                WHERE tenant_id = :tenant_id
                  AND id = :plan_id
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "draft_reply": draft_reply,
            },
        ).mappings().one_or_none()

        if row is None:
            raise LookupError(f"Response plan not found: {plan_id}")

        return _row_to_plan(row)


def _row_to_plan(row: Any) -> StoredResponsePlan:
    return StoredResponsePlan(
        plan_id=row["id"],
        conversation_id=row["conversation_id"],
        message_id=row["message_id"],
        decision=row["decision"],
        reason_code=row["reason_code"],
        risk_level=row["risk_level"],
        policy_rule_key=row["policy_rule_key"],
        knowledge_item_ids=list(row["knowledge_item_ids"] or []),
        knowledge_keys=list(row["knowledge_keys"] or []),
        allowed_claims=list(row["allowed_claims"] or []),
        forbidden_claims=list(row["forbidden_claims"] or []),
        reply_goal=row["reply_goal"],
        draft_reply=row["draft_reply"],
        planner_version=row["planner_version"],
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
