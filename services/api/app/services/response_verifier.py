import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine

from app.repositories.knowledge import get_published_knowledge_by_keys
from app.repositories.response_plans import StoredResponsePlan
from app.repositories.response_verifications import (
    StoredResponseVerification,
    upsert_response_verification,
)

VERIFIER_VERSION = "0.9.0"
MAX_REPLY_LENGTH = 650
STOPWORDS = {
    "a", "al", "algo", "como", "con", "cuando", "de", "del", "el", "en",
    "es", "esta", "este", "la", "las", "lo", "los", "o", "para", "por",
    "que", "se", "si", "su", "sus", "un", "una", "y",
}
RISK_PATTERNS = (
    r"(?:\$|usd|dolares?|pesos?)\s*\d",
    r"\b(precio|costo|valor|presupuesto)\b",
    r"\b(garantizamos|garantizado|aseguramos|confirmado)\b",
    r"\b(perfil|viga|columna|carga|resistencia|calculo estructural)\b",
)


@dataclass(frozen=True)
class VerificationDecision:
    status: str
    reason_code: str
    checks: dict[str, Any]
    unsupported_claims: list[str]
    verifier_version: str = VERIFIER_VERSION


def preview_verification(
    *,
    engine: Engine,
    tenant_slug: str,
    decision: str,
    draft_reply: str | None,
    knowledge_keys: list[str],
    forbidden_claims: list[str] | None = None,
) -> VerificationDecision:
    return _verify(
        engine=engine,
        tenant_slug=tenant_slug,
        decision=decision,
        draft_reply=draft_reply,
        knowledge_keys=knowledge_keys,
        forbidden_claims=forbidden_claims or [],
    )


def verify_and_record_response(
    *, engine: Engine, tenant_slug: str, plan: StoredResponsePlan
) -> StoredResponseVerification:
    decision = _verify(
        engine=engine,
        tenant_slug=tenant_slug,
        decision=plan.decision,
        draft_reply=plan.draft_reply,
        knowledge_keys=plan.knowledge_keys,
        forbidden_claims=plan.forbidden_claims,
    )
    return upsert_response_verification(
        engine=engine,
        tenant_slug=tenant_slug,
        plan_id=plan.plan_id,
        conversation_id=plan.conversation_id,
        message_id=plan.message_id,
        status=decision.status,
        reason_code=decision.reason_code,
        checks=decision.checks,
        unsupported_claims=decision.unsupported_claims,
        verifier_version=decision.verifier_version,
    )


def _verify(
    *,
    engine: Engine,
    tenant_slug: str,
    decision: str,
    draft_reply: str | None,
    knowledge_keys: list[str],
    forbidden_claims: list[str],
) -> VerificationDecision:
    if decision in {"HANDOFF", "IGNORE"}:
        return VerificationDecision(
            status="SKIPPED",
            reason_code="no_automatic_reply_planned",
            checks={"decision": decision},
            unsupported_claims=[],
        )

    draft = (draft_reply or "").strip()
    checks: dict[str, Any] = {
        "decision": decision,
        "draft_present": bool(draft),
        "length": len(draft),
        "knowledge_keys": knowledge_keys,
    }
    problems: list[str] = []

    if not draft:
        problems.append("El borrador está vacío.")
    if len(draft) > MAX_REPLY_LENGTH:
        problems.append(f"El borrador supera {MAX_REPLY_LENGTH} caracteres.")

    normalized_draft = _normalize(draft)
    matched_risks = [pattern for pattern in RISK_PATTERNS if re.search(pattern, normalized_draft)]
    checks["matched_risk_patterns"] = matched_risks
    if matched_risks:
        problems.append("El borrador contiene lenguaje comercial o técnico restringido.")

    matched_forbidden = [
        claim
        for claim in forbidden_claims
        if _phrase_matches(normalized_draft, _normalize(claim))
    ]
    checks["matched_forbidden_claims"] = matched_forbidden
    if matched_forbidden:
        problems.extend(matched_forbidden)

    if decision == "ASK":
        question_count = draft.count("?") + draft.count("¿")
        checks["question_mark_count"] = question_count
        if question_count < 1 or question_count > 4:
            problems.append("La solicitud de datos debe contener una o dos preguntas breves.")
    elif decision == "ANSWER":
        knowledge = get_published_knowledge_by_keys(
            engine=engine,
            tenant_slug=tenant_slug,
            external_keys=knowledge_keys,
        )
        checks["published_sources"] = [item.external_key for item in knowledge]
        checks["all_sources_low_risk"] = bool(knowledge) and all(
            item.risk_class == "low" for item in knowledge
        )
        if not knowledge:
            problems.append("No hay fuentes publicadas para respaldar la respuesta.")
        elif not checks["all_sources_low_risk"]:
            problems.append("Alguna fuente requiere revisión humana.")

        source_text = " ".join(item.content for item in knowledge)
        unsupported_sentences = _find_unsupported_sentences(draft, source_text)
        checks["unsupported_sentences"] = unsupported_sentences
        problems.extend(unsupported_sentences)

        source_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", source_text))
        draft_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", draft))
        new_numbers = sorted(draft_numbers - source_numbers)
        checks["new_numbers"] = new_numbers
        if new_numbers:
            problems.append("El borrador introduce números que no aparecen en las fuentes.")

    if problems:
        return VerificationDecision(
            status="REJECTED",
            reason_code="draft_not_safely_supported",
            checks=checks,
            unsupported_claims=_unique(problems),
        )

    return VerificationDecision(
        status="APPROVED",
        reason_code="draft_safely_supported",
        checks=checks,
        unsupported_claims=[],
    )


def _find_unsupported_sentences(draft: str, source_text: str) -> list[str]:
    normalized_source = _normalize(source_text)
    source_tokens = _significant_tokens(normalized_source)
    unsupported: list[str] = []

    for sentence in re.split(r"(?<=[.!?])\s+|\n+", draft):
        sentence = sentence.strip()
        if not sentence:
            continue
        normalized_sentence = _normalize(sentence)
        tokens = _significant_tokens(normalized_sentence)
        if not tokens:
            continue
        if normalized_sentence in normalized_source:
            continue
        overlap = len(tokens & source_tokens) / max(len(tokens), 1)
        if overlap < 0.72:
            unsupported.append(sentence)
    return unsupported


def _phrase_matches(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    if needle in haystack:
        return True
    needle_tokens = _significant_tokens(needle)
    if len(needle_tokens) < 3:
        return False
    haystack_tokens = _significant_tokens(haystack)
    return len(needle_tokens & haystack_tokens) / len(needle_tokens) >= 0.8


def _significant_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value)
        if len(token) > 2 and token not in STOPWORDS
    }


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"\s+", " ", without_marks).strip()


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
