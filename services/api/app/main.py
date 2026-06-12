import json

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_engine
from app.repositories.webhook_events import store_webhook_event
from app.schemas import WebhookAccepted
from app.security import verify_meta_signature
from app.settings import get_settings
from app.webhooks import extract_whatsapp_event_identity

settings = get_settings()

app = FastAPI(
    title="Stöll Assist API",
    version="0.2.0",
    description="Webhook, policy and handoff core for a multi-tenant WhatsApp assistant.",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}


@app.get("/webhooks/whatsapp", response_class=PlainTextResponse)
async def verify_whatsapp_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> str:
    if hub_mode != "subscribe" or hub_verify_token != settings.meta_verify_token:
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return hub_challenge or ""


@app.post("/webhooks/whatsapp", response_model=WebhookAccepted)
async def receive_whatsapp_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> WebhookAccepted:
    raw_body = await request.body()

    if not verify_meta_signature(raw_body, x_hub_signature_256, settings.meta_app_secret):
        raise HTTPException(status_code=401, detail="Invalid Meta signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object")

    provider_event_id, event_kind = extract_whatsapp_event_identity(payload, raw_body)

    try:
        stored = store_webhook_event(
            engine=get_engine(),
            tenant_slug=settings.default_tenant_slug,
            provider="whatsapp",
            provider_event_id=provider_event_id,
            event_kind=event_kind,
            payload=payload,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook could not be persisted",
        ) from exc

    # The next increment will enqueue the stored event for asynchronous processing.
    return WebhookAccepted(event_id=stored.event_id, duplicate=stored.duplicate)
