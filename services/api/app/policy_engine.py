import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import Engine

from app.repositories.policies import PolicyRuleRecord, list_policy_rules
from app.schemas import PolicyAction


@dataclass(frozen=True)
class PolicyDecisionResult:
    decision: PolicyAction
    matched_rule_key: str | None
    risk_level: str
    reason: str
    matched_evidence: list[str]


def evaluate_text(
    *, engine: Engine, tenant_slug: str, message_text: str
) -> PolicyDecisionResult:
    normalized = normalize_text(message_text)
    if not normalized:
        return PolicyDecisionResult(
            decision=PolicyAction.IGNORE,
            matched_rule_key=None,
            risk_level="low",
            reason="El mensaje no contiene texto evaluable.",
            matched_evidence=[],
        )

    for rule in list_policy_rules(
        engine=engine,
        tenant_slug=tenant_slug,
        enabled_only=True,
    ):
        evidence = _match_rule(normalized, rule)
        if evidence:
            action = PolicyAction(rule.action)
            return PolicyDecisionResult(
                decision=action,
                matched_rule_key=rule.rule_key,
                risk_level=rule.risk_level,
                reason=rule.description,
                matched_evidence=evidence,
            )

    return PolicyDecisionResult(
        decision=PolicyAction.ALLOW,
        matched_rule_key=None,
        risk_level="low",
        reason="No se activó ninguna regla determinista de bloqueo.",
        matched_evidence=[],
    )


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    lowered = without_marks.lower()
    return re.sub(r"\s+", " ", lowered).strip()


def _match_rule(normalized_text: str, rule: PolicyRuleRecord) -> list[str]:
    matches: list[str] = []

    terms = rule.config.get("terms", [])
    if isinstance(terms, list):
        for raw_term in terms:
            term = normalize_text(str(raw_term))
            if term and term in normalized_text:
                matches.append(f"term:{raw_term}")

    patterns = rule.config.get("patterns", [])
    if isinstance(patterns, list):
        for raw_pattern in patterns:
            pattern = str(raw_pattern)
            if re.search(pattern, normalized_text, flags=re.IGNORECASE):
                matches.append(f"regex:{pattern}")

    mode = str(rule.config.get("match_mode", "any")).lower()
    if mode == "all":
        expected = len(terms) + len(patterns)
        return matches if expected > 0 and len(matches) == expected else []

    return matches
