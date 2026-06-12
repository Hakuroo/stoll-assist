from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Engine

from app.normalization import normalize_whatsapp_messages
from app.repositories.messages import persist_inbound_messages_with_context
from app.repositories.webhook_events import complete_webhook_event
from app.services.drafting_providers import DraftingProvider
from app.services.llm_drafting import draft_response_if_enabled
from app.services.conversation_state import request_handoff
from app.services.outbound_pipeline import stage_verified_response
from app.services.policy_service import evaluate_and_apply_policy
from app.services.response_planner import plan_and_record_response
from app.services.response_verifier import verify_and_record_response
from app.settings import get_settings


@dataclass(frozen=True)
class ProcessingResult:
    status: str
    normalized_messages: int
    policy_handoffs: int = 0
    response_plans: int = 0
    response_verifications: int = 0
    rejected_drafts: int = 0
    outbound_drafts: int = 0


def process_whatsapp_webhook(
    *,
    engine: Engine,
    event_id: UUID,
    tenant_slug: str,
    payload: dict[str, Any],
    drafting_provider: DraftingProvider | None = None,
    llm_drafting_enabled: bool | None = None,
) -> ProcessingResult:
    try:
        normalized = normalize_whatsapp_messages(payload)
        persisted = persist_inbound_messages_with_context(
            engine=engine,
            tenant_slug=tenant_slug,
            messages=normalized,
            include_existing=True,
        )

        settings = get_settings()
        policy_handoffs = 0
        response_plans = 0
        response_verifications = 0
        rejected_drafts = 0
        outbound_drafts = 0

        for message in persisted:
            result = evaluate_and_apply_policy(
                engine=engine,
                tenant_slug=tenant_slug,
                message=message,
                agent_name=settings.agent_name,
            )
            if result.handoff_triggered:
                policy_handoffs += 1

            plan = plan_and_record_response(
                engine=engine,
                tenant_slug=tenant_slug,
                message=message,
                policy=result.decision,
            )
            response_plans += 1

            drafting = draft_response_if_enabled(
                engine=engine,
                tenant_slug=tenant_slug,
                message=message,
                plan=plan,
                settings=settings,
                provider=drafting_provider,
                enabled=llm_drafting_enabled,
            )
            plan = drafting.plan

            if drafting.defer_verification:
                continue

            if drafting.should_handoff:
                request_handoff(
                    engine=engine,
                    tenant_slug=tenant_slug,
                    conversation_id=message.conversation_id,
                    requested_by=settings.agent_name,
                    reason_code="llm_drafting_handoff",
                    summary="La etapa de redaccion asistida no pudo generar un borrador seguro.",
                )
                continue

            verification = verify_and_record_response(
                engine=engine,
                tenant_slug=tenant_slug,
                plan=plan,
            )
            response_verifications += 1

            if verification.status == "REJECTED":
                rejected_drafts += 1
                request_handoff(
                    engine=engine,
                    tenant_slug=tenant_slug,
                    conversation_id=message.conversation_id,
                    requested_by=settings.agent_name,
                    reason_code="draft_verification_failed",
                    summary="El borrador automático no pudo validarse contra el conocimiento aprobado.",
                )

            if verification.status == "APPROVED":
                outbound = stage_verified_response(
                    engine=engine,
                    tenant_slug=tenant_slug,
                    verification=verification,
                )
                if outbound is not None:
                    outbound_drafts += 1

        terminal_status = "PROCESSED" if normalized else "IGNORED"
        complete_webhook_event(
            engine=engine,
            event_id=event_id,
            status=terminal_status,
        )
        return ProcessingResult(
            status=terminal_status,
            normalized_messages=len(persisted),
            policy_handoffs=policy_handoffs,
            response_plans=response_plans,
            response_verifications=response_verifications,
            rejected_drafts=rejected_drafts,
            outbound_drafts=outbound_drafts,
        )
    except Exception as exc:
        complete_webhook_event(
            engine=engine,
            event_id=event_id,
            status="FAILED",
            error_message=str(exc)[:2000],
        )
        raise
