from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.schemas import WebhookAccepted
from app.security import verify_meta_signature
from app.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="Stöll Assist API",
    version="0.1.0",
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

    # Importante: responder rápido y procesar de forma asíncrona.
    # Próxima etapa:
    # 1. persistir payload;
    # 2. deduplicar por provider_message_id;
    # 3. publicar un job;
    # 4. devolver 200 sin esperar al modelo.
    return WebhookAccepted()
