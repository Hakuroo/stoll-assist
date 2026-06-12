from dataclasses import dataclass
from os import getpid
from uuid import uuid4

from sqlalchemy import Engine

from app.repositories.knowledge import get_published_knowledge_by_keys
from app.repositories.messages import PersistedInboundMessage, list_recent_conversation_messages
from app.repositories.response_generations import (
    StoredResponseGeneration,
    claim_response_generation,
    finish_response_generation_claim,
)
from app.repositories.response_plans import StoredResponsePlan, update_response_plan_draft
from app.services.drafting_provider_factory import create_drafting_provider
from app.services.drafting_providers import (
    PROMPT_VERSION,
    DraftingContext,
    DraftingProvider,
    DraftingProviderError,
    DraftingStructuredOutput,
    build_drafting_request,
    validate_drafting_output,
)
from app.settings import Settings, get_settings


@dataclass(frozen=True)
class DraftingStageResult:
    plan: StoredResponsePlan
    should_handoff: bool
    defer_verification: bool
    generation: StoredResponseGeneration | None


def draft_response_if_enabled(
    *,
    engine: Engine,
    tenant_slug: str,
    message: PersistedInboundMessage,
    plan: StoredResponsePlan,
    settings: Settings | None = None,
    provider: DraftingProvider | None = None,
    enabled: bool | None = None,
) -> DraftingStageResult:
    settings = settings or get_settings()
    llm_enabled = settings.llm_drafting_enabled if enabled is None else enabled

    if plan.decision not in {"ANSWER", "ASK"}:
        return DraftingStageResult(
            plan=plan,
            should_handoff=False,
            defer_verification=False,
            generation=None,
        )
    if not llm_enabled:
        return DraftingStageResult(
            plan=plan,
            should_handoff=False,
            defer_verification=False,
            generation=None,
        )

    context = build_drafting_context(
        engine=engine,
        tenant_slug=tenant_slug,
        message=message,
        plan=plan,
        settings=settings,
    )
    provider = provider or create_drafting_provider(settings)
    request = build_drafting_request(context)
    lease_owner = _new_lease_owner()
    claim = claim_response_generation(
        engine=engine,
        tenant_slug=tenant_slug,
        conversation_id=plan.conversation_id,
        message_id=plan.message_id,
        response_plan_id=plan.plan_id,
        provider=provider.name,
        model=provider.model,
        prompt_version=PROMPT_VERSION,
        input_hash=request.input_hash,
        lease_owner=lease_owner,
        lease_seconds=settings.llm_drafting_lease_seconds,
    )
    if not claim.claimed:
        return _apply_existing_generation(
            engine=engine,
            tenant_slug=tenant_slug,
            plan=plan,
            generation=claim.generation,
            context=context,
        )

    try:
        result = provider.draft(context)
        validate_drafting_output(
            output=result.output,
            available_knowledge_keys={item.external_key for item in context.knowledge_items},
            max_questions=context.max_questions,
        )
    except (DraftingProviderError, ValueError) as exc:
        generation = finish_response_generation_claim(
            engine=engine,
            tenant_slug=tenant_slug,
            generation_id=claim.generation.generation_id,
            lease_owner=lease_owner,
            status="FAILED",
            error_code=_error_code(exc),
            error_message=str(exc),
        )
        return _apply_existing_generation(
            engine=engine,
            tenant_slug=tenant_slug,
            plan=plan,
            generation=generation,
            context=context,
        )

    status = "HANDOFF" if result.output.should_handoff else "COMPLETED"
    generation = finish_response_generation_claim(
        engine=engine,
        tenant_slug=tenant_slug,
        generation_id=claim.generation.generation_id,
        lease_owner=lease_owner,
        status=status,
        provider_request_id=result.provider_request_id,
        structured_output=result.output.model_dump(mode="json"),
        latency_ms=result.latency_ms,
        token_usage=result.token_usage,
    )
    return _apply_existing_generation(
        engine=engine,
        tenant_slug=tenant_slug,
        plan=plan,
        generation=generation,
        context=context,
    )


def build_drafting_context(
    *,
    engine: Engine,
    tenant_slug: str,
    message: PersistedInboundMessage,
    plan: StoredResponsePlan,
    settings: Settings,
) -> DraftingContext:
    knowledge_items = get_published_knowledge_by_keys(
        engine=engine,
        tenant_slug=tenant_slug,
        external_keys=plan.knowledge_keys,
    )
    history = list_recent_conversation_messages(
        engine=engine,
        tenant_slug=tenant_slug,
        conversation_id=plan.conversation_id,
        limit=settings.llm_drafting_max_history_messages,
    )
    return DraftingContext(
        tenant_slug=tenant_slug,
        agent_name=settings.agent_name,
        agent_disclosure=settings.agent_disclosure,
        decision=plan.decision,
        reply_goal=plan.reply_goal,
        current_message=message.body_text or "",
        recent_history=history,
        knowledge_items=knowledge_items,
        allowed_claims=plan.allowed_claims,
        forbidden_claims=plan.forbidden_claims,
        fallback_draft=plan.draft_reply,
    )


def _apply_existing_generation(
    *,
    engine: Engine,
    tenant_slug: str,
    plan: StoredResponsePlan,
    generation: StoredResponseGeneration,
    context: DraftingContext,
) -> DraftingStageResult:
    if generation.status == "HANDOFF":
        return DraftingStageResult(
            plan=plan,
            should_handoff=True,
            defer_verification=False,
            generation=generation,
        )
    if generation.status == "FAILED":
        return _fallback_or_handoff(plan=plan, generation=generation)
    if generation.status == "IN_PROGRESS":
        return DraftingStageResult(
            plan=plan,
            should_handoff=False,
            defer_verification=True,
            generation=generation,
        )
    if generation.status in {"COMPLETED", "SUCCEEDED"} and generation.structured_output:
        output = DraftingStructuredOutput.model_validate(generation.structured_output)
        validate_drafting_output(
            output=output,
            available_knowledge_keys={item.external_key for item in context.knowledge_items},
            max_questions=context.max_questions,
        )
        updated_plan = update_response_plan_draft(
            engine=engine,
            tenant_slug=tenant_slug,
            plan_id=plan.plan_id,
            draft_reply=output.draft_reply.strip(),
        )
        return DraftingStageResult(
            plan=updated_plan,
            should_handoff=False,
            defer_verification=False,
            generation=generation,
        )
    return _fallback_or_handoff(plan=plan, generation=generation)


def _fallback_or_handoff(
    *,
    plan: StoredResponsePlan,
    generation: StoredResponseGeneration,
) -> DraftingStageResult:
    if (plan.draft_reply or "").strip():
        return DraftingStageResult(
            plan=plan,
            should_handoff=False,
            defer_verification=False,
            generation=generation,
        )
    return DraftingStageResult(
        plan=plan,
        should_handoff=True,
        defer_verification=False,
        generation=generation,
    )


def _new_lease_owner() -> str:
    return f"pid-{getpid()}:{uuid4()}"


def _error_code(exc: Exception) -> str:
    if isinstance(exc, DraftingProviderError):
        return exc.code
    if isinstance(exc, ValueError):
        return "invalid_output"
    return "drafting_error"
