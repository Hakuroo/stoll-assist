from uuid import UUID

from sqlalchemy import Engine

from app.repositories.outbox import (
    OutboxTransitionError,
    StoredOutboundMessage,
    claim_outbound_for_send,
    mark_outbound_send_failed,
    mark_outbound_sent,
)
from app.whatsapp_provider import WhatsAppProvider, WhatsAppProviderError


def send_approved_outbound(
    *,
    engine: Engine,
    tenant_slug: str,
    outbound_id: UUID,
    operator_name: str,
    provider: WhatsAppProvider,
    send_enabled: bool,
) -> StoredOutboundMessage:
    if not send_enabled:
        raise OutboxTransitionError("WhatsApp sending is disabled")

    outbound = claim_outbound_for_send(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound_id,
        operator_name=operator_name,
    )
    if outbound.status == "SENT" and outbound.provider_message_id:
        return outbound

    try:
        result = provider.send_text(
            to=outbound.recipient,
            body=outbound.body_text,
            outbound_id=outbound.outbound_id,
        )
    except WhatsAppProviderError as exc:
        mark_outbound_send_failed(
            engine=engine,
            tenant_slug=tenant_slug,
            outbound_id=outbound.outbound_id,
            error_message=_safe_error_message(exc),
            operator_name=operator_name,
        )
        raise

    return mark_outbound_sent(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound.outbound_id,
        provider_message_id=result.provider_message_id,
        operator_name=operator_name,
    )


def _safe_error_message(exc: WhatsAppProviderError) -> str:
    parts = [str(exc)]
    if exc.status_code is not None:
        parts.append(f"status={exc.status_code}")
    if exc.latency_ms is not None:
        parts.append(f"latency_ms={exc.latency_ms}")
    if exc.retryable:
        parts.append("retryable=true")
    return "; ".join(parts)
