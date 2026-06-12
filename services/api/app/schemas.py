from enum import StrEnum
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
