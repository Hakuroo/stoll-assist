from enum import StrEnum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationState(StrEnum):
    AUTOMATED = "AUTOMATED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"
    CLOSED = "CLOSED"


class OperatorRole(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class Decision(StrEnum):
    ANSWER = "ANSWER"
    ASK = "ASK"
    HANDOFF = "HANDOFF"
    IGNORE = "IGNORE"


class AgentDecision(BaseModel):
    intent: str
    decision: Decision
    risk_level: str = Field(pattern="^(low|medium|high)$")
    requires_human: bool
    reason_code: str | None = None
    missing_fields: list[str] = []
    knowledge_keys: list[str] = []
    proposed_reply: str | None = None
    extracted_lead_fields: dict[str, Any] = {}
    unsupported_claims: list[str] = []


class WebhookAccepted(BaseModel):
    accepted: bool = True
    duplicate: bool = False
    event_id: UUID
    event_status: str
    normalized_messages: int = 0


class AuthLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    tenant_slug: str | None = Field(default=None, min_length=1, max_length=120)


class AuthUserResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    tenant_id: UUID
    tenant_slug: str
    tenant_name: str
    role: OperatorRole
    expires_at: datetime


class OperatorUserResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    status: str
    role: OperatorRole
    membership_active: bool
    last_login_at: datetime | None
    created_at: datetime


class OperatorUserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=10, max_length=1024)
    role: OperatorRole
    active: bool = True


class HandoffRequest(BaseModel):
    reason_code: str = Field(min_length=2, max_length=80)
    summary: str | None = Field(default=None, max_length=2000)
    requested_by: str = Field(default="system", min_length=2, max_length=120)


class OperatorActionRequest(BaseModel):
    operator_name: str = Field(min_length=2, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class ActiveHandoffResponse(BaseModel):
    handoff_id: UUID
    reason_code: str
    summary: str | None
    status: str
    requested_by: str | None
    taken_by: str | None
    created_at: datetime
    taken_at: datetime | None


class ConversationResponse(BaseModel):
    conversation_id: UUID
    tenant_slug: str
    contact_id: UUID
    display_name: str | None
    whatsapp_user_id: str
    phone_e164: str | None
    state: ConversationState
    automation_allowed: bool
    assigned_operator: str | None
    last_state_reason: str | None
    state_changed_at: datetime
    last_message_at: datetime | None
    created_at: datetime
    state_version: int
    active_handoff: ActiveHandoffResponse | None

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "ConversationResponse":
        handoff = None
        if snapshot.active_handoff is not None:
            handoff = ActiveHandoffResponse(
                handoff_id=snapshot.active_handoff.handoff_id,
                reason_code=snapshot.active_handoff.reason_code,
                summary=snapshot.active_handoff.summary,
                status=snapshot.active_handoff.status,
                requested_by=snapshot.active_handoff.requested_by,
                taken_by=snapshot.active_handoff.taken_by,
                created_at=snapshot.active_handoff.created_at,
                taken_at=snapshot.active_handoff.taken_at,
            )

        return cls(
            conversation_id=snapshot.conversation_id,
            tenant_slug=snapshot.tenant_slug,
            contact_id=snapshot.contact_id,
            display_name=snapshot.display_name,
            whatsapp_user_id=snapshot.whatsapp_user_id,
            phone_e164=snapshot.phone_e164,
            state=snapshot.state,
            automation_allowed=snapshot.state == ConversationState.AUTOMATED,
            assigned_operator=snapshot.assigned_operator,
            last_state_reason=snapshot.last_state_reason,
            state_changed_at=snapshot.state_changed_at,
            last_message_at=snapshot.last_message_at,
            created_at=snapshot.created_at,
            state_version=snapshot.state_version,
            active_handoff=handoff,
        )


class StateTransitionResponse(BaseModel):
    changed: bool
    conversation: ConversationResponse


class PolicyAction(StrEnum):
    ALLOW = "ALLOW"
    HANDOFF = "HANDOFF"
    IGNORE = "IGNORE"


class PolicyPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PolicyDecisionResponse(BaseModel):
    decision: PolicyAction
    matched_rule_key: str | None
    risk_level: str = Field(pattern="^(low|medium|high)$")
    reason: str
    matched_evidence: list[str] = Field(default_factory=list)


class PolicyRuleResponse(BaseModel):
    rule_key: str
    description: str
    action: PolicyAction
    priority: int
    enabled: bool
    risk_level: str


class KnowledgeImportResponse(BaseModel):
    files: int
    created: int
    updated: int
    unchanged: int


class KnowledgePublishRequest(BaseModel):
    approved_by: str = Field(min_length=2, max_length=120)
    version: int | None = Field(default=None, ge=1)


class KnowledgeItemResponse(BaseModel):
    item_id: UUID
    external_key: str
    title: str
    content: str
    status: str
    risk_class: str
    version: int
    source_path: str | None
    allowed_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    approved_by: str | None
    approved_at: datetime | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_item(cls, item: Any) -> "KnowledgeItemResponse":
        return cls(
            item_id=item.item_id,
            external_key=item.external_key,
            title=item.title,
            content=item.content,
            status=item.status,
            risk_class=item.risk_class,
            version=item.version,
            source_path=item.source_path,
            allowed_claims=item.allowed_claims,
            forbidden_claims=item.forbidden_claims,
            approved_by=item.approved_by,
            approved_at=item.approved_at,
            published_at=item.published_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10)


class KnowledgeSearchHitResponse(BaseModel):
    item_id: UUID
    external_key: str
    title: str
    excerpt: str
    risk_class: str
    version: int
    allowed_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    score: float


class KnowledgeSearchResponse(BaseModel):
    query: str
    hits: list[KnowledgeSearchHitResponse] = Field(default_factory=list)


class ResponsePlanPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    conversation_state: ConversationState = ConversationState.AUTOMATED


class ResponsePlanResponse(BaseModel):
    decision: Decision
    reason_code: str
    risk_level: str = Field(pattern="^(low|medium|high)$")
    policy_rule_key: str | None
    knowledge_item_ids: list[str] = Field(default_factory=list)
    knowledge_keys: list[str] = Field(default_factory=list)
    allowed_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    reply_goal: str
    draft_reply: str | None
    planner_version: str

    @classmethod
    def from_plan(cls, plan: Any) -> "ResponsePlanResponse":
        return cls(
            decision=plan.decision,
            reason_code=plan.reason_code,
            risk_level=plan.risk_level,
            policy_rule_key=plan.policy_rule_key,
            knowledge_item_ids=plan.knowledge_item_ids,
            knowledge_keys=plan.knowledge_keys,
            allowed_claims=plan.allowed_claims,
            forbidden_claims=plan.forbidden_claims,
            reply_goal=plan.reply_goal,
            draft_reply=plan.draft_reply,
            planner_version=plan.planner_version,
        )


class ResponseVerificationPreviewRequest(BaseModel):
    decision: Decision
    draft_reply: str | None = Field(default=None, max_length=4000)
    knowledge_keys: list[str] = Field(default_factory=list, max_length=10)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=50)


class ResponseVerificationResponse(BaseModel):
    status: str = Field(pattern="^(APPROVED|REJECTED|SKIPPED)$")
    reason_code: str
    checks: dict[str, Any] = Field(default_factory=dict)
    unsupported_claims: list[str] = Field(default_factory=list)
    verifier_version: str

    @classmethod
    def from_verification(cls, verification: Any) -> "ResponseVerificationResponse":
        return cls(
            status=verification.status,
            reason_code=verification.reason_code,
            checks=verification.checks,
            unsupported_claims=verification.unsupported_claims,
            verifier_version=verification.verifier_version,
        )


class OutboundStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    QUEUED = "QUEUED"
    SENT = "SENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class OutboundApprovalRequest(BaseModel):
    operator_name: str = Field(min_length=2, max_length=120)


class OutboundRejectionRequest(BaseModel):
    operator_name: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=3, max_length=2000)


class OutboundMessageResponse(BaseModel):
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
    status: OutboundStatus
    requires_review: bool
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    rejection_reason: str | None
    provider_message_id: str | None
    send_attempt_count: int
    last_attempt_at: datetime | None
    lease_expires_at: datetime | None
    sent_at: datetime | None
    failed_at: datetime | None
    unknown_at: datetime | None
    delivery_status: str | None
    delivered_at: datetime | None
    read_at: datetime | None
    provider_failed_at: datetime | None
    delivery_error_code: str | None
    delivery_error_message: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_outbound(cls, item: Any) -> "OutboundMessageResponse":
        return cls(
            outbound_id=item.outbound_id,
            conversation_id=item.conversation_id,
            in_reply_to_message_id=item.in_reply_to_message_id,
            plan_id=item.plan_id,
            verification_id=item.verification_id,
            channel=item.channel,
            recipient=item.recipient,
            display_name=item.display_name,
            body_text=item.body_text,
            body_sha256=item.body_sha256,
            status=item.status,
            requires_review=item.requires_review,
            approved_by=item.approved_by,
            approved_at=item.approved_at,
            rejected_by=item.rejected_by,
            rejected_at=item.rejected_at,
            rejection_reason=item.rejection_reason,
            provider_message_id=item.provider_message_id,
            send_attempt_count=item.send_attempt_count,
            last_attempt_at=item.last_attempt_at,
            lease_expires_at=item.lease_expires_at,
            sent_at=item.sent_at,
            failed_at=item.failed_at,
            unknown_at=item.unknown_at,
            delivery_status=item.delivery_status,
            delivered_at=item.delivered_at,
            read_at=item.read_at,
            provider_failed_at=item.provider_failed_at,
            delivery_error_code=item.delivery_error_code,
            delivery_error_message=item.delivery_error_message,
            error_message=item.error_message,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class OutboxSendConfigResponse(BaseModel):
    whatsapp_send_enabled: bool


class DashboardConversationSummaryResponse(BaseModel):
    conversation_id: UUID
    display_name: str | None
    whatsapp_user_id: str
    phone_e164: str | None
    state: ConversationState
    assigned_operator: str | None
    last_state_reason: str | None
    last_message_at: datetime | None
    last_message_body: str | None
    last_message_direction: str | None
    last_message_type: str | None
    active_handoff_status: str | None
    requires_human: bool
    created_at: datetime

    @classmethod
    def from_item(cls, item: Any) -> "DashboardConversationSummaryResponse":
        return cls(
            conversation_id=item.conversation_id,
            display_name=item.display_name,
            whatsapp_user_id=item.whatsapp_user_id,
            phone_e164=item.phone_e164,
            state=item.state,
            assigned_operator=item.assigned_operator,
            last_state_reason=item.last_state_reason,
            last_message_at=item.last_message_at,
            last_message_body=item.last_message_body,
            last_message_direction=item.last_message_direction,
            last_message_type=item.last_message_type,
            active_handoff_status=item.active_handoff_status,
            requires_human=item.state in {"HUMAN_REQUIRED", "HUMAN_ACTIVE"},
            created_at=item.created_at,
        )


class DashboardMessageResponse(BaseModel):
    message_id: UUID
    direction: str
    message_type: str
    body_text: str | None
    created_at: datetime

    @classmethod
    def from_item(cls, item: Any) -> "DashboardMessageResponse":
        return cls(
            message_id=item.message_id,
            direction=item.direction,
            message_type=item.message_type,
            body_text=item.body_text,
            created_at=item.created_at,
        )


class DashboardResponsePlanResponse(BaseModel):
    plan_id: UUID
    message_id: UUID
    decision: Decision
    reason_code: str
    risk_level: str
    policy_rule_key: str | None
    knowledge_keys: list[str] = Field(default_factory=list)
    allowed_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    reply_goal: str
    draft_reply: str | None
    planner_version: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_item(cls, item: Any) -> "DashboardResponsePlanResponse":
        return cls(
            plan_id=item.plan_id,
            message_id=item.message_id,
            decision=item.decision,
            reason_code=item.reason_code,
            risk_level=item.risk_level,
            policy_rule_key=item.policy_rule_key,
            knowledge_keys=item.knowledge_keys,
            allowed_claims=item.allowed_claims,
            forbidden_claims=item.forbidden_claims,
            reply_goal=item.reply_goal,
            draft_reply=item.draft_reply,
            planner_version=item.planner_version,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class DashboardVerificationResponse(BaseModel):
    verification_id: UUID
    plan_id: UUID
    message_id: UUID
    status: str
    reason_code: str
    checks: dict[str, Any] = Field(default_factory=dict)
    unsupported_claims: list[str] = Field(default_factory=list)
    verifier_version: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_item(cls, item: Any) -> "DashboardVerificationResponse":
        return cls(
            verification_id=item.verification_id,
            plan_id=item.plan_id,
            message_id=item.message_id,
            status=item.status,
            reason_code=item.reason_code,
            checks=item.checks,
            unsupported_claims=item.unsupported_claims,
            verifier_version=item.verifier_version,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class DashboardHandoffResponse(BaseModel):
    handoff_id: UUID
    reason_code: str
    summary: str | None
    status: str
    requested_by: str | None
    taken_by: str | None
    resolved_by: str | None
    resolution_note: str | None
    created_at: datetime
    taken_at: datetime | None
    resolved_at: datetime | None

    @classmethod
    def from_item(cls, item: Any) -> "DashboardHandoffResponse":
        return cls(
            handoff_id=item.handoff_id,
            reason_code=item.reason_code,
            summary=item.summary,
            status=item.status,
            requested_by=item.requested_by,
            taken_by=item.taken_by,
            resolved_by=item.resolved_by,
            resolution_note=item.resolution_note,
            created_at=item.created_at,
            taken_at=item.taken_at,
            resolved_at=item.resolved_at,
        )


class DashboardConversationDetailResponse(BaseModel):
    conversation: DashboardConversationSummaryResponse
    messages: list[DashboardMessageResponse]
    response_plans: list[DashboardResponsePlanResponse]
    verifications: list[DashboardVerificationResponse]
    handoffs: list[DashboardHandoffResponse]

    @classmethod
    def from_detail(cls, detail: Any) -> "DashboardConversationDetailResponse":
        return cls(
            conversation=DashboardConversationSummaryResponse.from_item(detail.summary),
            messages=[DashboardMessageResponse.from_item(item) for item in detail.messages],
            response_plans=[
                DashboardResponsePlanResponse.from_item(item)
                for item in detail.response_plans
            ],
            verifications=[
                DashboardVerificationResponse.from_item(item)
                for item in detail.verifications
            ],
            handoffs=[DashboardHandoffResponse.from_item(item) for item in detail.handoffs],
        )


class DashboardKnowledgeSourceResponse(BaseModel):
    external_key: str
    title: str
    version: int
    status: str

    @classmethod
    def from_item(cls, item: Any) -> "DashboardKnowledgeSourceResponse":
        return cls(
            external_key=item.external_key,
            title=item.title,
            version=item.version,
            status=item.status,
        )


class DashboardOutboxReviewItemResponse(BaseModel):
    outbound_id: UUID
    conversation_id: UUID
    in_reply_to_message_id: UUID
    plan_id: UUID
    verification_id: UUID
    recipient: str
    display_name: str | None
    body_text: str
    status: OutboundStatus
    requires_review: bool
    provider_message_id: str | None
    send_attempt_count: int
    last_attempt_at: datetime | None
    lease_expires_at: datetime | None
    sent_at: datetime | None
    failed_at: datetime | None
    unknown_at: datetime | None
    delivery_status: str | None
    delivered_at: datetime | None
    read_at: datetime | None
    provider_failed_at: datetime | None
    delivery_error_code: str | None
    delivery_error_message: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    customer_message_text: str | None
    customer_message_type: str
    plan: DashboardResponsePlanResponse
    verification: DashboardVerificationResponse
    knowledge_sources: list[DashboardKnowledgeSourceResponse]

    @classmethod
    def from_item(cls, item: Any) -> "DashboardOutboxReviewItemResponse":
        return cls(
            outbound_id=item.outbound_id,
            conversation_id=item.conversation_id,
            in_reply_to_message_id=item.in_reply_to_message_id,
            plan_id=item.plan_id,
            verification_id=item.verification_id,
            recipient=item.recipient,
            display_name=item.display_name,
            body_text=item.body_text,
            status=item.status,
            requires_review=item.requires_review,
            provider_message_id=item.provider_message_id,
            send_attempt_count=item.send_attempt_count,
            last_attempt_at=item.last_attempt_at,
            lease_expires_at=item.lease_expires_at,
            sent_at=item.sent_at,
            failed_at=item.failed_at,
            unknown_at=item.unknown_at,
            delivery_status=item.delivery_status,
            delivered_at=item.delivered_at,
            read_at=item.read_at,
            provider_failed_at=item.provider_failed_at,
            delivery_error_code=item.delivery_error_code,
            delivery_error_message=item.delivery_error_message,
            error_message=item.error_message,
            created_at=item.created_at,
            updated_at=item.updated_at,
            customer_message_text=item.customer_message_text,
            customer_message_type=item.customer_message_type,
            plan=DashboardResponsePlanResponse.from_item(item.plan),
            verification=DashboardVerificationResponse.from_item(item.verification),
            knowledge_sources=[
                DashboardKnowledgeSourceResponse.from_item(source)
                for source in item.knowledge_sources
            ],
        )
