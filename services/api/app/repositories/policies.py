import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True)
class PolicyRuleRecord:
    rule_key: str
    description: str
    action: str
    priority: int
    enabled: bool
    config: dict[str, Any]

    @property
    def risk_level(self) -> str:
        value = str(self.config.get("risk_level", "medium"))
        return value if value in {"low", "medium", "high"} else "medium"


def list_policy_rules(
    *, engine: Engine, tenant_slug: str, enabled_only: bool = True
) -> list[PolicyRuleRecord]:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        rows = connection.execute(
            text(
                """
                SELECT rule_key, description, action, priority, enabled, config
                FROM policy_rules
                WHERE tenant_id = :tenant_id
                  AND (:enabled_only = false OR enabled = true)
                ORDER BY priority ASC, rule_key ASC
                """
            ),
            {"tenant_id": tenant_id, "enabled_only": enabled_only},
        ).mappings().all()

    return [
        PolicyRuleRecord(
            rule_key=row["rule_key"],
            description=row["description"],
            action=row["action"],
            priority=row["priority"],
            enabled=row["enabled"],
            config=dict(row["config"] or {}),
        )
        for row in rows
    ]


def record_policy_evaluation(
    *,
    engine: Engine,
    tenant_slug: str,
    message_id: UUID,
    conversation_id: UUID,
    decision: str,
    matched_rule_key: str | None,
    risk_level: str,
    reason: str,
    evidence: list[str],
) -> None:
    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        payload = json.dumps(
            {
                "matched_rule_key": matched_rule_key,
                "risk_level": risk_level,
                "reason": reason,
                "evidence": evidence,
            },
            ensure_ascii=False,
        )

        connection.execute(
            text(
                """
                INSERT INTO policy_evaluations (
                    tenant_id,
                    conversation_id,
                    message_id,
                    decision,
                    matched_rule_key,
                    risk_level,
                    reason,
                    evidence
                )
                VALUES (
                    :tenant_id,
                    :conversation_id,
                    :message_id,
                    :decision,
                    :matched_rule_key,
                    :risk_level,
                    :reason,
                    CAST(:evidence AS jsonb)
                )
                ON CONFLICT (tenant_id, message_id)
                DO UPDATE SET
                    decision = EXCLUDED.decision,
                    matched_rule_key = EXCLUDED.matched_rule_key,
                    risk_level = EXCLUDED.risk_level,
                    reason = EXCLUDED.reason,
                    evidence = EXCLUDED.evidence,
                    evaluated_at = now()
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "decision": decision,
                "matched_rule_key": matched_rule_key,
                "risk_level": risk_level,
                "reason": reason,
                "evidence": json.dumps(evidence, ensure_ascii=False),
            },
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
                    'POLICY_EVALUATED',
                    :decision,
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "decision": decision,
                "payload": payload,
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
