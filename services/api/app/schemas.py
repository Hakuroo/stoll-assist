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
