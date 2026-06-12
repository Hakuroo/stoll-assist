import json

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_engine
from app.queue import enqueue_webhook_event
from app.routers.conversations import router as conversations_router
from app.routers.knowledge import router as knowledge_router
from app.routers.policies import router as policies_router
from app.routers.planner import router as planner_router
from app.repositories.webhook_events import (
    complete_webhook_event,
    mark_webhook_queued,
    store_webhook_event,
)
from app.schemas import WebhookAccepted
from app.security import verify_meta_signature
from app.settings import get_settings
from app.webhooks import extract_whatsapp_event_identity

settings = get_settings()

app = FastAPI(
    title="Stöll Assist API",
    version="0.8.0",
    description="Webhook, policy and handoff core for a multi-tenant WhatsApp assistant.",
)

app.include_router(conversations_router)
app.include_router(knowledge_router)
app.include_router(policies_router)
app.include_router(planner_router)


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
    engine = get_engine()

    try:
        stored = store_webhook_event(
            engine=engine,
            tenant_slug=settings.default_tenant_slug,
            provider="whatsapp",
            provider_event_id=provider_event_id,
            event_kind=event_kind,
            payload=payload,
        )

        queued = mark_webhook_queued(engine=engine, event_id=stored.event_id)
        if not queued.claimed:
            return WebhookAccepted(
                event_id=stored.event_id,
                duplicate=stored.duplicate,
                event_status=queued.status,
                normalized_messages=0,
            )

        try:
            enqueue_webhook_event(stored.event_id)
        except RedisError as exc:
            complete_webhook_event(
                engine=engine,
                event_id=stored.event_id,
                status="FAILED",
                error_message="Queue unavailable",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook was persisted but could not be queued",
            ) from exc
    except HTTPException:
        raise
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook could not be persisted",
        ) from exc

    return WebhookAccepted(
        event_id=stored.event_id,
        duplicate=stored.duplicate,
        event_status="QUEUED",
        normalized_messages=0,
    )
