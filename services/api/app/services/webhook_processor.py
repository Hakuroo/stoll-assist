from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Engine

from app.normalization import normalize_whatsapp_messages
from app.repositories.messages import persist_inbound_messages
from app.repositories.webhook_events import complete_webhook_event


@dataclass(frozen=True)
class ProcessingResult:
    status: str
    normalized_messages: int


def process_whatsapp_webhook(
    *,
    engine: Engine,
    event_id: UUID,
    tenant_slug: str,
    payload: dict[str, Any],
) -> ProcessingResult:
    try:
        normalized = normalize_whatsapp_messages(payload)
        inserted_count = persist_inbound_messages(
            engine=engine,
            tenant_slug=tenant_slug,
            messages=normalized,
        )

        terminal_status = "PROCESSED" if normalized else "IGNORED"
        complete_webhook_event(
            engine=engine,
            event_id=event_id,
            status=terminal_status,
        )
        return ProcessingResult(
            status=terminal_status,
            normalized_messages=inserted_count,
        )
    except Exception as exc:
        complete_webhook_event(
            engine=engine,
            event_id=event_id,
            status="FAILED",
            error_message=str(exc)[:2000],
        )
        raise
