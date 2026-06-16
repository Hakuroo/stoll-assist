from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from app.schemas import ConversationState


@dataclass(frozen=True)
class DashboardConversationSummary:
    conversation_id: UUID
    display_name: str | None
    whatsapp_user_id: str
    phone_e164: str | None
    state: str
    assigned_operator: str | None
    last_state_reason: str | None
    last_message_at: Any | None
    last_message_body: str | None
    last_message_direction: str | None
    last_message_type: str | None
    active_handoff_status: str | None
    created_at: Any


@dataclass(frozen=True)
class DashboardMessage:
    message_id: UUID
    direction: str
    message_type: str
    body_text: str | None
    created_at: Any


@dataclass(frozen=True)
class DashboardResponsePlan:
    plan_id: UUID
    message_id: UUID
    decision: str
    reason_code: str
    risk_level: str
    policy_rule_key: str | None
    knowledge_keys: list[str]
    allowed_claims: list[str]
    forbidden_claims: list[str]
    reply_goal: str
    draft_reply: str | None
    planner_version: str
    created_at: Any
    updated_at: Any


@dataclass(frozen=True)
class DashboardVerification:
    verification_id: UUID
    plan_id: UUID
    message_id: UUID
    status: str
    reason_code: str
    checks: dict[str, Any]
    unsupported_claims: list[str]
    verifier_version: str
    created_at: Any
    updated_at: Any


@dataclass(frozen=True)
class DashboardHandoff:
    handoff_id: UUID
    reason_code: str
    summary: str | None
    status: str
    requested_by: str | None
    taken_by: str | None
    resolved_by: str | None
    resolution_note: str | None
    created_at: Any
    taken_at: Any | None
    resolved_at: Any | None


@dataclass(frozen=True)
class DashboardConversationDetail:
    summary: DashboardConversationSummary
    messages: list[DashboardMessage]
    response_plans: list[DashboardResponsePlan]
    verifications: list[DashboardVerification]
    handoffs: list[DashboardHandoff]


@dataclass(frozen=True)
class DashboardKnowledgeSource:
    external_key: str
    title: str
    version: int
    status: str


@dataclass(frozen=True)
class DashboardOutboxReviewItem:
    outbound_id: UUID
    conversation_id: UUID
    in_reply_to_message_id: UUID
    plan_id: UUID
    verification_id: UUID
    recipient: str
    display_name: str | None
    body_text: str
    status: str
    requires_review: bool
    provider_message_id: str | None
    send_attempt_count: int
    last_attempt_at: Any
    lease_owner: str | None
    lease_expires_at: Any
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
    customer_message_text: str | None
    customer_message_type: str
    plan: DashboardResponsePlan
    verification: DashboardVerification
    knowledge_sources: list[DashboardKnowledgeSource]


def list_dashboard_conversations(
    *,
    engine: Engine,
    tenant_slug: str,
    state_filter: ConversationState | None = None,
    limit: int = 100,
) -> list[DashboardConversationSummary]:
    limit = min(max(limit, 1), 200)
    state_clause = ""
    params: dict[str, Any] = {"limit": limit}
    if state_filter is not None:
        state_clause = "AND c.state = :state_filter"
        params["state_filter"] = state_filter.value

    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        params["tenant_id"] = tenant_id
        rows = connection.execute(
            text(
                f"""
                SELECT
                    c.id AS conversation_id,
                    ct.display_name,
                    ct.whatsapp_user_id,
                    ct.phone_e164,
                    c.state,
                    c.assigned_operator,
                    c.last_state_reason,
                    c.last_message_at,
                    c.created_at,
                    lm.body_text AS last_message_body,
                    lm.direction AS last_message_direction,
                    lm.message_type AS last_message_type,
                    h.status AS active_handoff_status
                FROM conversations c
                JOIN contacts ct ON ct.id = c.contact_id
                LEFT JOIN LATERAL (
                    SELECT body_text, direction, message_type
                    FROM messages m
                    WHERE m.tenant_id = c.tenant_id
                      AND m.conversation_id = c.id
                    ORDER BY m.created_at DESC
                    LIMIT 1
                ) lm ON true
                LEFT JOIN LATERAL (
                    SELECT status
                    FROM handoffs h
                    WHERE h.tenant_id = c.tenant_id
                      AND h.conversation_id = c.id
                      AND h.status IN ('OPEN', 'TAKEN')
                    ORDER BY h.created_at DESC
                    LIMIT 1
                ) h ON true
                WHERE c.tenant_id = :tenant_id
                  {state_clause}
                ORDER BY
                    CASE WHEN c.state = 'HUMAN_REQUIRED' THEN 0 ELSE 1 END,
                    COALESCE(c.last_message_at, c.created_at) DESC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [_row_to_summary(row) for row in rows]


def get_dashboard_conversation_detail(
    *, engine: Engine, tenant_slug: str, conversation_id: UUID
) -> DashboardConversationDetail:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        summary_row = connection.execute(
            text(
                """
                SELECT *
                FROM (
                    SELECT
                        c.id AS conversation_id,
                        ct.display_name,
                        ct.whatsapp_user_id,
                        ct.phone_e164,
                        c.state,
                        c.assigned_operator,
                        c.last_state_reason,
                        c.last_message_at,
                        c.created_at,
                        lm.body_text AS last_message_body,
                        lm.direction AS last_message_direction,
                        lm.message_type AS last_message_type,
                        h.status AS active_handoff_status
                    FROM conversations c
                    JOIN contacts ct ON ct.id = c.contact_id
                    LEFT JOIN LATERAL (
                        SELECT body_text, direction, message_type
                        FROM messages m
                        WHERE m.tenant_id = c.tenant_id
                          AND m.conversation_id = c.id
                        ORDER BY m.created_at DESC
                        LIMIT 1
                    ) lm ON true
                    LEFT JOIN LATERAL (
                        SELECT status
                        FROM handoffs h
                        WHERE h.tenant_id = c.tenant_id
                          AND h.conversation_id = c.id
                          AND h.status IN ('OPEN', 'TAKEN')
                        ORDER BY h.created_at DESC
                        LIMIT 1
                    ) h ON true
                    WHERE c.tenant_id = :tenant_id
                      AND c.id = :conversation_id
                ) q
                """
            ),
            {"tenant_id": tenant_id, "conversation_id": conversation_id},
        ).mappings().one_or_none()
        if summary_row is None:
            raise LookupError(f"Conversation not found: {conversation_id}")

        messages = connection.execute(
            text(
                """
                SELECT id, direction, message_type, body_text, created_at
                FROM messages
                WHERE tenant_id = :tenant_id
                  AND conversation_id = :conversation_id
                ORDER BY created_at ASC
                LIMIT 200
                """
            ),
            {"tenant_id": tenant_id, "conversation_id": conversation_id},
        ).mappings().all()
        plans = connection.execute(
            text(
                """
                SELECT *
                FROM response_plans
                WHERE tenant_id = :tenant_id
                  AND conversation_id = :conversation_id
                ORDER BY created_at DESC
                LIMIT 20
                """
            ),
            {"tenant_id": tenant_id, "conversation_id": conversation_id},
        ).mappings().all()
        verifications = connection.execute(
            text(
                """
                SELECT *
                FROM response_verifications
                WHERE tenant_id = :tenant_id
                  AND conversation_id = :conversation_id
                ORDER BY created_at DESC
                LIMIT 20
                """
            ),
            {"tenant_id": tenant_id, "conversation_id": conversation_id},
        ).mappings().all()
        handoffs = connection.execute(
            text(
                """
                SELECT *
                FROM handoffs
                WHERE tenant_id = :tenant_id
                  AND conversation_id = :conversation_id
                ORDER BY created_at DESC
                LIMIT 20
                """
            ),
            {"tenant_id": tenant_id, "conversation_id": conversation_id},
        ).mappings().all()

    return DashboardConversationDetail(
        summary=_row_to_summary(summary_row),
        messages=[
            DashboardMessage(
                message_id=row["id"],
                direction=row["direction"],
                message_type=row["message_type"],
                body_text=row["body_text"],
                created_at=row["created_at"],
            )
            for row in messages
        ],
        response_plans=[_row_to_plan(row) for row in plans],
        verifications=[_row_to_verification(row) for row in verifications],
        handoffs=[_row_to_handoff(row) for row in handoffs],
    )


def list_dashboard_outbox_review(
    *,
    engine: Engine,
    tenant_slug: str,
    status_filter: str | None = None,
    limit: int = 100,
) -> list[DashboardOutboxReviewItem]:
    limit = min(max(limit, 1), 200)
    status_clause = "AND om.status IN ('PENDING_REVIEW', 'APPROVED', 'QUEUED', 'FAILED', 'UNKNOWN', 'SENT')"
    params: dict[str, Any] = {"limit": limit}
    if status_filter is not None:
        status_clause = "AND om.status = :status_filter"
        params["status_filter"] = status_filter

    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        params["tenant_id"] = tenant_id
        rows = connection.execute(
            text(
                f"""
                SELECT
                    om.id AS outbound_id,
                    om.conversation_id,
                    om.in_reply_to_message_id,
                    om.plan_id,
                    om.verification_id,
                    om.recipient,
                    ct.display_name,
                    om.body_text,
                    om.status,
                    om.requires_review,
                    om.provider_message_id,
                    om.send_attempt_count,
                    om.last_attempt_at,
                    om.lease_owner,
                    om.lease_expires_at,
                    om.sent_at,
                    om.failed_at,
                    om.unknown_at,
                    om.delivery_status,
                    om.delivered_at,
                    om.read_at,
                    om.provider_failed_at,
                    om.delivery_error_code,
                    om.delivery_error_message,
                    om.error_message,
                    om.created_at,
                    om.updated_at,
                    m.body_text AS customer_message_text,
                    m.message_type AS customer_message_type,
                    rp.id AS response_plan_id,
                    rp.message_id AS plan_message_id,
                    rp.decision,
                    rp.reason_code AS plan_reason_code,
                    rp.risk_level,
                    rp.policy_rule_key,
                    rp.knowledge_keys,
                    rp.allowed_claims,
                    rp.forbidden_claims,
                    rp.reply_goal,
                    rp.draft_reply,
                    rp.planner_version,
                    rp.created_at AS plan_created_at,
                    rp.updated_at AS plan_updated_at,
                    rv.id AS response_verification_id,
                    rv.message_id AS verification_message_id,
                    rv.status AS verification_status,
                    rv.reason_code AS verification_reason_code,
                    rv.checks,
                    rv.unsupported_claims,
                    rv.verifier_version,
                    rv.created_at AS verification_created_at,
                    rv.updated_at AS verification_updated_at,
                    ks.knowledge_sources
                FROM outbound_messages om
                JOIN conversations c ON c.id = om.conversation_id
                JOIN contacts ct ON ct.id = c.contact_id
                JOIN messages m ON m.id = om.in_reply_to_message_id
                JOIN response_plans rp ON rp.id = om.plan_id
                JOIN response_verifications rv ON rv.id = om.verification_id
                LEFT JOIN LATERAL (
                    SELECT COALESCE(
                        jsonb_agg(
                            jsonb_build_object(
                                'external_key', source.external_key,
                                'title', source.title,
                                'version', source.version,
                                'status', source.status
                            )
                            ORDER BY source.external_key
                        ),
                        '[]'::jsonb
                    ) AS knowledge_sources
                    FROM (
                        SELECT DISTINCT ON (k.external_key)
                            k.external_key,
                            k.title,
                            k.version,
                            k.status
                        FROM knowledge_items k
                        WHERE k.tenant_id = om.tenant_id
                          AND k.external_key IN (
                              SELECT jsonb_array_elements_text(rp.knowledge_keys)
                          )
                        ORDER BY
                            k.external_key,
                            CASE WHEN k.status = 'published' THEN 0 ELSE 1 END,
                            k.version DESC
                    ) source
                ) ks ON true
                WHERE om.tenant_id = :tenant_id
                  {status_clause}
                ORDER BY om.created_at DESC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [_row_to_outbox_item(row) for row in rows]


def _row_to_summary(row: Any) -> DashboardConversationSummary:
    return DashboardConversationSummary(
        conversation_id=row["conversation_id"],
        display_name=row["display_name"],
        whatsapp_user_id=row["whatsapp_user_id"],
        phone_e164=row["phone_e164"],
        state=row["state"],
        assigned_operator=row["assigned_operator"],
        last_state_reason=row["last_state_reason"],
        last_message_at=row["last_message_at"],
        last_message_body=row["last_message_body"],
        last_message_direction=row["last_message_direction"],
        last_message_type=row["last_message_type"],
        active_handoff_status=row["active_handoff_status"],
        created_at=row["created_at"],
    )


def _row_to_plan(row: Any) -> DashboardResponsePlan:
    return DashboardResponsePlan(
        plan_id=row["id"],
        message_id=row["message_id"],
        decision=row["decision"],
        reason_code=row["reason_code"],
        risk_level=row["risk_level"],
        policy_rule_key=row["policy_rule_key"],
        knowledge_keys=list(row["knowledge_keys"] or []),
        allowed_claims=list(row["allowed_claims"] or []),
        forbidden_claims=list(row["forbidden_claims"] or []),
        reply_goal=row["reply_goal"],
        draft_reply=row["draft_reply"],
        planner_version=row["planner_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_verification(row: Any) -> DashboardVerification:
    return DashboardVerification(
        verification_id=row["id"],
        plan_id=row["plan_id"],
        message_id=row["message_id"],
        status=row["status"],
        reason_code=row["reason_code"],
        checks=dict(row["checks"] or {}),
        unsupported_claims=list(row["unsupported_claims"] or []),
        verifier_version=row["verifier_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_handoff(row: Any) -> DashboardHandoff:
    return DashboardHandoff(
        handoff_id=row["id"],
        reason_code=row["reason_code"],
        summary=row["summary"],
        status=row["status"],
        requested_by=row["requested_by"],
        taken_by=row["taken_by"],
        resolved_by=row["resolved_by"],
        resolution_note=row["resolution_note"],
        created_at=row["created_at"],
        taken_at=row["taken_at"],
        resolved_at=row["resolved_at"],
    )


def _row_to_outbox_item(row: Any) -> DashboardOutboxReviewItem:
    plan = DashboardResponsePlan(
        plan_id=row["response_plan_id"],
        message_id=row["plan_message_id"],
        decision=row["decision"],
        reason_code=row["plan_reason_code"],
        risk_level=row["risk_level"],
        policy_rule_key=row["policy_rule_key"],
        knowledge_keys=list(row["knowledge_keys"] or []),
        allowed_claims=list(row["allowed_claims"] or []),
        forbidden_claims=list(row["forbidden_claims"] or []),
        reply_goal=row["reply_goal"],
        draft_reply=row["draft_reply"],
        planner_version=row["planner_version"],
        created_at=row["plan_created_at"],
        updated_at=row["plan_updated_at"],
    )
    verification = DashboardVerification(
        verification_id=row["response_verification_id"],
        plan_id=row["response_plan_id"],
        message_id=row["verification_message_id"],
        status=row["verification_status"],
        reason_code=row["verification_reason_code"],
        checks=dict(row["checks"] or {}),
        unsupported_claims=list(row["unsupported_claims"] or []),
        verifier_version=row["verifier_version"],
        created_at=row["verification_created_at"],
        updated_at=row["verification_updated_at"],
    )
    return DashboardOutboxReviewItem(
        outbound_id=row["outbound_id"],
        conversation_id=row["conversation_id"],
        in_reply_to_message_id=row["in_reply_to_message_id"],
        plan_id=row["plan_id"],
        verification_id=row["verification_id"],
        recipient=row["recipient"],
        display_name=row["display_name"],
        body_text=row["body_text"],
        status=row["status"],
        requires_review=row["requires_review"],
        provider_message_id=row["provider_message_id"],
        send_attempt_count=row["send_attempt_count"],
        last_attempt_at=row["last_attempt_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
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
        customer_message_text=row["customer_message_text"],
        customer_message_type=row["customer_message_type"],
        plan=plan,
        verification=verification,
        knowledge_sources=[
            DashboardKnowledgeSource(
                external_key=item["external_key"],
                title=item["title"],
                version=item["version"],
                status=item["status"],
            )
            for item in list(row["knowledge_sources"] or [])
        ],
    )


def _get_active_tenant_id(connection: Connection, tenant_slug: str) -> UUID:
    tenant_id = connection.execute(
        text("SELECT id FROM tenants WHERE slug = :slug AND status = 'active'"),
        {"slug": tenant_slug},
    ).scalar_one_or_none()
    if tenant_id is None:
        raise LookupError(f"Active tenant not found: {tenant_slug}")
    return tenant_id
