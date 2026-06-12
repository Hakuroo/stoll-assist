import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine

from app.policy_engine import PolicyDecisionResult, evaluate_text
from app.repositories.knowledge import KnowledgeSearchHit, search_published_knowledge
from app.repositories.messages import PersistedInboundMessage
from app.repositories.response_plans import StoredResponsePlan, upsert_response_plan
from app.schemas import ConversationState, Decision, PolicyAction
from app.services.conversation_state import read_conversation

PLANNER_VERSION = "0.8.0"
MIN_KNOWLEDGE_SCORE = 0.04


@dataclass(frozen=True)
class ResponsePlanDecision:
    decision: Decision
    reason_code: str
    risk_level: str
    policy_rule_key: str | None
    knowledge_item_ids: list[str]
    knowledge_keys: list[str]
    allowed_claims: list[str]
    forbidden_claims: list[str]
    reply_goal: str
    draft_reply: str | None
    planner_version: str = PLANNER_VERSION


def preview_response_plan(
    *,
    engine: Engine,
    tenant_slug: str,
    text: str,
    conversation_state: ConversationState = ConversationState.AUTOMATED,
) -> ResponsePlanDecision:
    policy = evaluate_text(
        engine=engine,
        tenant_slug=tenant_slug,
        message_text=text,
    )
    return _build_plan(
        engine=engine,
        tenant_slug=tenant_slug,
        text=text,
        message_type="text",
        conversation_state=conversation_state,
        policy=policy,
    )


def plan_and_record_response(
    *,
    engine: Engine,
    tenant_slug: str,
    message: PersistedInboundMessage,
    policy: PolicyDecisionResult,
) -> StoredResponsePlan:
    snapshot = read_conversation(
        engine=engine,
        tenant_slug=tenant_slug,
        conversation_id=message.conversation_id,
    )
    plan = _build_plan(
        engine=engine,
        tenant_slug=tenant_slug,
        text=message.body_text or "",
        message_type=message.message_type,
        conversation_state=snapshot.state,
        policy=policy,
    )
    return upsert_response_plan(
        engine=engine,
        tenant_slug=tenant_slug,
        conversation_id=message.conversation_id,
        message_id=message.message_id,
        decision=plan.decision.value,
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


def _build_plan(
    *,
    engine: Engine,
    tenant_slug: str,
    text: str,
    message_type: str,
    conversation_state: ConversationState,
    policy: PolicyDecisionResult,
) -> ResponsePlanDecision:
    if policy.decision == PolicyAction.HANDOFF:
        return ResponsePlanDecision(
            decision=Decision.HANDOFF,
            reason_code=policy.matched_rule_key or "policy_handoff",
            risk_level=policy.risk_level,
            policy_rule_key=policy.matched_rule_key,
            knowledge_item_ids=[],
            knowledge_keys=[],
            allowed_claims=[],
            forbidden_claims=[],
            reply_goal="No responder automáticamente. La consulta debe ser revisada por una persona.",
            draft_reply=None,
        )

    if policy.decision == PolicyAction.IGNORE or message_type != "text" or not text.strip():
        return ResponsePlanDecision(
            decision=Decision.IGNORE,
            reason_code="unsupported_or_empty_message",
            risk_level="low",
            policy_rule_key=policy.matched_rule_key,
            knowledge_item_ids=[],
            knowledge_keys=[],
            allowed_claims=[],
            forbidden_claims=[],
            reply_goal="No generar una respuesta textual automática.",
            draft_reply=None,
        )

    if conversation_state != ConversationState.AUTOMATED:
        return ResponsePlanDecision(
            decision=Decision.IGNORE,
            reason_code="automation_suspended",
            risk_level="high",
            policy_rule_key=None,
            knowledge_item_ids=[],
            knowledge_keys=[],
            allowed_claims=[],
            forbidden_claims=[],
            reply_goal="Permanecer en silencio porque la conversación está bajo control humano o cerrada.",
            draft_reply=None,
        )

    normalized = _normalize(text)
    project_intake = _is_project_intake(normalized)
    informational = _is_information_question(normalized)

    hits = search_published_knowledge(
        engine=engine,
        tenant_slug=tenant_slug,
        query=text,
        limit=3,
    )

    # A vague project request may not lexically match the knowledge document.
    # Use a controlled fallback query that points only to approved intake knowledge.
    if project_intake and not _usable_hits(hits):
        hits = search_published_knowledge(
            engine=engine,
            tenant_slug=tenant_slug,
            query="información inicial evaluar obra ubicación medidas altura uso planos fotografías",
            limit=3,
        )

    usable = _usable_hits(hits)
    if not usable:
        return ResponsePlanDecision(
            decision=Decision.HANDOFF,
            reason_code="low_evidence",
            risk_level="medium",
            policy_rule_key=None,
            knowledge_item_ids=[],
            knowledge_keys=[],
            allowed_claims=[],
            forbidden_claims=[],
            reply_goal="No responder con información no respaldada. Derivar para revisión humana.",
            draft_reply=None,
        )

    if any(hit.risk_class != "low" for hit in usable):
        return _from_hits(
            decision=Decision.HANDOFF,
            reason_code="knowledge_requires_review",
            risk_level="high",
            hits=usable,
            reply_goal="No responder automáticamente porque el conocimiento recuperado no es de bajo riesgo.",
            draft_reply=None,
        )

    if project_intake and not informational:
        return _from_hits(
            decision=Decision.ASK,
            reason_code="collect_project_information",
            risk_level="low",
            hits=usable,
            reply_goal="Solicitar como máximo dos datos faltantes para continuar la evaluación inicial.",
            draft_reply=_build_intake_question(normalized),
        )

    return _from_hits(
        decision=Decision.ANSWER,
        reason_code="approved_knowledge_available",
        risk_level="low",
        hits=usable,
        reply_goal="Responder únicamente con el conocimiento aprobado y sin agregar promesas, precios ni conclusiones técnicas.",
        draft_reply=usable[0].content.strip(),
    )


def _from_hits(
    *,
    decision: Decision,
    reason_code: str,
    risk_level: str,
    hits: list[KnowledgeSearchHit],
    reply_goal: str,
    draft_reply: str | None,
) -> ResponsePlanDecision:
    allowed: list[str] = []
    forbidden: list[str] = []
    for hit in hits:
        allowed.extend(value for value in hit.allowed_claims if value not in allowed)
        forbidden.extend(value for value in hit.forbidden_claims if value not in forbidden)

    return ResponsePlanDecision(
        decision=decision,
        reason_code=reason_code,
        risk_level=risk_level,
        policy_rule_key=None,
        knowledge_item_ids=[str(hit.item_id) for hit in hits],
        knowledge_keys=[hit.external_key for hit in hits],
        allowed_claims=allowed,
        forbidden_claims=forbidden,
        reply_goal=reply_goal,
        draft_reply=draft_reply,
    )


def _usable_hits(hits: list[KnowledgeSearchHit]) -> list[KnowledgeSearchHit]:
    return [hit for hit in hits if hit.score >= MIN_KNOWLEDGE_SCORE]


def _is_project_intake(text: str) -> bool:
    project_terms = (
        "galpon",
        "tinglado",
        "estructura",
        "nave",
        "obra",
        "deposito",
        "techo",
    )
    intent_terms = ("necesito", "quiero", "busco", "hacer", "construir", "cotizar")
    return any(term in text for term in project_terms) and any(
        term in text for term in intent_terms
    )


def _is_information_question(text: str) -> bool:
    terms = (
        "que hacen",
        "que trabajos",
        "que servicios",
        "trabajan",
        "incluye",
        "modalidades",
        "que informacion",
        "que datos",
        "puedo enviar",
        "reciben planos",
        "solo estructura",
        "llave en mano",
    )
    return "?" in text or any(term in text for term in terms)


def _build_intake_question(text: str) -> str:
    has_dimensions = bool(
        re.search(r"\b\d+(?:[.,]\d+)?\s*(?:x|por)\s*\d+(?:[.,]\d+)?\b", text)
    )
    has_use = any(
        term in text
        for term in (
            "deposito",
            "deportivo",
            "industrial",
            "taller",
            "maquinaria",
            "comercial",
            "productivo",
        )
    )

    if not has_dimensions:
        return "Perfecto. ¿En qué localidad sería la obra y qué medidas aproximadas tendría?"
    if not has_use:
        return "Perfecto. ¿Qué uso tendría el proyecto y lo necesitás cerrado o tipo tinglado?"
    return "Perfecto. ¿Ya cuentan con platea o fundaciones y para qué fecha estiman realizar la obra?"


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"\s+", " ", without_marks).strip()
