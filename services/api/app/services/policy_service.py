from dataclasses import dataclass

from sqlalchemy import Engine

from app.policy_engine import PolicyDecisionResult, evaluate_text
from app.repositories.messages import PersistedInboundMessage
from app.repositories.policies import record_policy_evaluation
from app.schemas import ConversationState, PolicyAction
from app.services.conversation_state import read_conversation, request_handoff


@dataclass(frozen=True)
class AppliedPolicyResult:
    decision: PolicyDecisionResult
    handoff_triggered: bool


def evaluate_and_apply_policy(
    *,
    engine: Engine,
    tenant_slug: str,
    message: PersistedInboundMessage,
    agent_name: str,
) -> AppliedPolicyResult:
    if message.message_type != "text" or not message.body_text:
        decision = PolicyDecisionResult(
            decision=PolicyAction.IGNORE,
            matched_rule_key=None,
            risk_level="low",
            reason="El mensaje no es texto y no fue evaluado por reglas textuales.",
            matched_evidence=[],
        )
    else:
        decision = evaluate_text(
            engine=engine,
            tenant_slug=tenant_slug,
            message_text=message.body_text,
        )

    record_policy_evaluation(
        engine=engine,
        tenant_slug=tenant_slug,
        message_id=message.message_id,
        conversation_id=message.conversation_id,
        decision=decision.decision.value,
        matched_rule_key=decision.matched_rule_key,
        risk_level=decision.risk_level,
        reason=decision.reason,
        evidence=decision.matched_evidence,
    )

    handoff_triggered = False
    if decision.decision == PolicyAction.HANDOFF:
        snapshot = read_conversation(
            engine=engine,
            tenant_slug=tenant_slug,
            conversation_id=message.conversation_id,
        )
        if snapshot.state == ConversationState.AUTOMATED:
            excerpt = (message.body_text or "")[:500]
            request_handoff(
                engine=engine,
                tenant_slug=tenant_slug,
                conversation_id=message.conversation_id,
                requested_by=agent_name,
                reason_code=decision.matched_rule_key or "policy_handoff",
                summary=(
                    f"La política automática derivó el mensaje: {excerpt}"
                    if excerpt
                    else decision.reason
                ),
            )
            handoff_triggered = True

    return AppliedPolicyResult(
        decision=decision,
        handoff_triggered=handoff_triggered,
    )
