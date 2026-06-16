import re
from uuid import UUID
from uuid import uuid4

from sqlalchemy import Engine

from app.repositories.outbox import (
    OutboxTransitionError,
    StoredOutboundMessage,
    claim_outbound_for_send,
    mark_outbound_send_failed,
    mark_outbound_send_unknown,
    mark_outbound_sent,
)
from app.whatsapp_provider import (
    WhatsAppProvider,
    WhatsAppProviderError,
    WhatsAppProviderTimeout,
    WhatsAppProviderUncertainError,
)


def send_approved_outbound(
    *,
    engine: Engine,
    tenant_slug: str,
    outbound_id: UUID,
    operator_name: str,
    provider: WhatsAppProvider,
    send_enabled: bool,
    lease_seconds: int = 120,
) -> StoredOutboundMessage:
    if not send_enabled:
        raise OutboxTransitionError("WhatsApp sending is disabled")

    lease_owner = f"{operator_name}:{uuid4()}"
    outbound = claim_outbound_for_send(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound_id,
        operator_name=operator_name,
        lease_owner=lease_owner,
        lease_seconds=lease_seconds,
    )
    if outbound.status == "SENT" and outbound.provider_message_id:
        return outbound
    if outbound.current_send_attempt_id is None:
        raise OutboxTransitionError("Outbound message send attempt was not recorded")

    try:
        result = provider.send_text(
            to=outbound.recipient,
            body=outbound.body_text,
            outbound_id=outbound.outbound_id,
        )
    except WhatsAppProviderError as exc:
        result_is_unknown = isinstance(
            exc,
            (WhatsAppProviderTimeout, WhatsAppProviderUncertainError),
        )
        marker = (
            mark_outbound_send_unknown
            if result_is_unknown
            else mark_outbound_send_failed
        )
        updated = marker(
            engine=engine,
            tenant_slug=tenant_slug,
            outbound_id=outbound.outbound_id,
            attempt_id=outbound.current_send_attempt_id,
            lease_owner=lease_owner,
            error_message=_safe_error_message(exc),
            error_type=exc.error_type,
            request_started=exc.request_started,
            latency_ms=exc.latency_ms,
            provider_message_id=exc.provider_message_id,
            operator_name=operator_name,
        )
        if result_is_unknown:
            return updated
        raise

    return mark_outbound_sent(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound.outbound_id,
        attempt_id=outbound.current_send_attempt_id,
        lease_owner=lease_owner,
        provider_message_id=result.provider_message_id,
        latency_ms=result.latency_ms,
        operator_name=operator_name,
    )


def _safe_error_message(exc: WhatsAppProviderError) -> str:
    parts = [_redact_sensitive_text(str(exc))]
    parts.append(f"type={exc.error_type}")
    if exc.status_code is not None:
        parts.append(f"status={exc.status_code}")
    if exc.latency_ms is not None:
        parts.append(f"latency_ms={exc.latency_ms}")
    if exc.request_started:
        parts.append("request_started=true")
    if exc.retryable:
        parts.append("retryable=true")
    return "; ".join(parts)


def _redact_sensitive_text(value: str) -> str:
    redacted = re.sub(
        r"(?i)(access_token|token|secret)=([^;\s]+)",
        r"\1=[redacted]",
        value,
    )
    redacted = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [redacted]", redacted)
    return redacted
