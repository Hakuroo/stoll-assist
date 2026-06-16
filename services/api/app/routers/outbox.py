from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from app.auth import OPERATE_ROLES, READ_ROLES, AuthContext, require_roles
from app.database import get_engine
from app.repositories.outbox import (
    OutboxTransitionError,
    approve_outbound_message,
    get_outbound_by_provider_message_id,
    get_outbound_message,
    list_outbound_messages,
    reject_outbound_message,
)
from app.schemas import (
    OutboxSendConfigResponse,
    OutboundApprovalRequest,
    OutboundMessageResponse,
    OutboundRejectionRequest,
    OutboundStatus,
)
from app.services.whatsapp_sender import send_approved_outbound
from app.settings import Settings, get_settings
from app.whatsapp_provider import WhatsAppProviderError, get_whatsapp_provider

router = APIRouter(prefix="/operator/outbox", tags=["outbox-review"])


@router.get("", response_model=list[OutboundMessageResponse])
def list_messages(
    status_filter: OutboundStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(require_roles(*READ_ROLES)),
) -> list[OutboundMessageResponse]:
    try:
        items = list_outbound_messages(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            status_filter=None if status_filter is None else status_filter.value,
            limit=limit,
        )
        return [OutboundMessageResponse.from_outbound(item) for item in items]
    except (LookupError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Outbox could not be listed") from exc


@router.get("/send-config", response_model=OutboxSendConfigResponse)
def send_config(
    auth: AuthContext = Depends(require_roles(*READ_ROLES)),
    settings: Settings = Depends(get_settings),
) -> OutboxSendConfigResponse:
    _ = auth
    return OutboxSendConfigResponse(
        whatsapp_send_enabled=settings.whatsapp_send_enabled,
    )


@router.get(
    "/by-provider-message/{provider_message_id}",
    response_model=OutboundMessageResponse,
)
def by_provider_message(
    provider_message_id: str,
    auth: AuthContext = Depends(require_roles(*READ_ROLES)),
) -> OutboundMessageResponse:
    try:
        item = get_outbound_by_provider_message_id(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            provider_message_id=provider_message_id,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Outbound message not found")
        return OutboundMessageResponse.from_outbound(item)
    except HTTPException:
        raise
    except (LookupError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Outbox could not be read") from exc


@router.get("/{outbound_id}", response_model=OutboundMessageResponse)
def get_message(
    outbound_id: UUID,
    auth: AuthContext = Depends(require_roles(*READ_ROLES)),
) -> OutboundMessageResponse:
    try:
        item = get_outbound_message(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            outbound_id=outbound_id,
        )
        return OutboundMessageResponse.from_outbound(item)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Outbox could not be read") from exc


@router.post("/{outbound_id}/approve", response_model=OutboundMessageResponse)
def approve(
    outbound_id: UUID,
    request: OutboundApprovalRequest,
    auth: AuthContext = Depends(require_roles(*OPERATE_ROLES, csrf=True)),
) -> OutboundMessageResponse:
    try:
        item = approve_outbound_message(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            outbound_id=outbound_id,
            operator_name=auth.display_name,
        )
        return OutboundMessageResponse.from_outbound(item)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OutboxTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Outbound message could not be approved") from exc


@router.post("/{outbound_id}/reject", response_model=OutboundMessageResponse)
def reject(
    outbound_id: UUID,
    request: OutboundRejectionRequest,
    auth: AuthContext = Depends(require_roles(*OPERATE_ROLES, csrf=True)),
) -> OutboundMessageResponse:
    try:
        item = reject_outbound_message(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            outbound_id=outbound_id,
            operator_name=auth.display_name,
            reason=request.reason,
        )
        return OutboundMessageResponse.from_outbound(item)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OutboxTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Outbound message could not be rejected") from exc


@router.post("/{outbound_id}/send", response_model=OutboundMessageResponse)
def send(
    outbound_id: UUID,
    auth: AuthContext = Depends(require_roles(*OPERATE_ROLES, csrf=True)),
    settings: Settings = Depends(get_settings),
) -> OutboundMessageResponse:
    try:
        item = send_approved_outbound(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            outbound_id=outbound_id,
            operator_name=auth.display_name,
            provider=get_whatsapp_provider(settings),
            send_enabled=settings.whatsapp_send_enabled,
            lease_seconds=settings.whatsapp_send_lease_seconds,
        )
        return OutboundMessageResponse.from_outbound(item)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OutboxTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WhatsAppProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="WhatsApp provider send failed",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Outbound message could not be sent") from exc
