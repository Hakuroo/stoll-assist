from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Engine

from app.normalization import normalize_whatsapp_messages
from app.repositories.messages import persist_inbound_messages_with_context
from app.repositories.webhook_events import complete_webhook_event
from app.services.policy_service import evaluate_and_apply_policy
from app.settings import get_settings


@dataclass(frozen=True)
class ProcessingResult:
    status: str
    normalized_messages: int
    policy_handoffs: int = 0


def process_whatsapp_webhook(
    *,
    engine: Engine,
    event_id: UUID,
    tenant_slug: str,
    payload: dict[str, Any],
) -> ProcessingResult:
    try:
        normalized = normalize_whatsapp_messages(payload)
        persisted = persist_inbound_messages_with_context(
            engine=engine,
            tenant_slug=tenant_slug,
            messages=normalized,
        )

        settings = get_settings()
        policy_handoffs = 0
        for message in persisted:
            result = evaluate_and_apply_policy(
                engine=engine,
                tenant_slug=tenant_slug,
                message=message,
                agent_name=settings.agent_name,
            )
            if result.handoff_triggered:
                policy_handoffs += 1

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
        )
    except Exception as exc:
        complete_webhook_event(
            engine=engine,
            event_id=event_id,
            status="FAILED",
            error_message=str(exc)[:2000],
        )
        raise
