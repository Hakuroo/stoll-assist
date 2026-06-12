from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_engine
from app.policy_engine import evaluate_text
from app.repositories.policies import list_policy_rules
from app.schemas import (
    PolicyDecisionResponse,
    PolicyPreviewRequest,
    PolicyRuleResponse,
)
from app.settings import get_settings

router = APIRouter(prefix="/operator/policies", tags=["operator-policies"])
settings = get_settings()


@router.get("", response_model=list[PolicyRuleResponse])
def get_rules() -> list[PolicyRuleResponse]:
    try:
        rules = list_policy_rules(
            engine=get_engine(),
            tenant_slug=settings.default_tenant_slug,
            enabled_only=False,
        )
        return [
            PolicyRuleResponse(
                rule_key=rule.rule_key,
                description=rule.description,
                action=rule.action,
                priority=rule.priority,
                enabled=rule.enabled,
                risk_level=rule.risk_level,
            )
            for rule in rules
        ]
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Policies could not be read") from exc


@router.post("/preview", response_model=PolicyDecisionResponse)
def preview_policy(request: PolicyPreviewRequest) -> PolicyDecisionResponse:
    try:
        result = evaluate_text(
            engine=get_engine(),
            tenant_slug=settings.default_tenant_slug,
            message_text=request.text,
        )
        return PolicyDecisionResponse(
            decision=result.decision,
            matched_rule_key=result.matched_rule_key,
            risk_level=result.risk_level,
            reason=result.reason,
            matched_evidence=result.matched_evidence,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (SQLAlchemyError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Policy could not be evaluated") from exc
