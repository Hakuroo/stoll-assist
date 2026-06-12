from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import get_engine
from app.main import app
from app.repositories.knowledge import import_knowledge_directory, publish_knowledge_item
from app.repositories.outbox import get_outbound_by_provider_message_id
from app.repositories.webhook_events import store_webhook_event
from app.services.webhook_processor import process_whatsapp_webhook
from app.settings import get_settings


SAFE_QUESTION = "Que informacion necesitan para evaluar una obra?"


@pytest.fixture(scope="module")
def app_context():
    try:
        settings = get_settings()
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Integration database is not available: {exc}")

    with engine.begin() as connection:
        previous_mode = connection.execute(
            text("SELECT outbound_mode FROM tenants WHERE slug = :slug"),
            {"slug": settings.default_tenant_slug},
        ).scalar_one()
        connection.execute(
            text("UPDATE tenants SET outbound_mode = 'REVIEW_REQUIRED' WHERE slug = :slug"),
            {"slug": settings.default_tenant_slug},
        )

    knowledge_dir = _knowledge_dir(settings.knowledge_config_path)
    import_knowledge_directory(
        engine=engine,
        tenant_slug=settings.default_tenant_slug,
        directory=knowledge_dir,
    )
    for external_key in ("KB-001", "KB-002"):
        publish_knowledge_item(
            engine=engine,
            tenant_slug=settings.default_tenant_slug,
            external_key=external_key,
            approved_by="pytest",
        )

    yield engine, settings.default_tenant_slug

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tenants SET outbound_mode = :mode WHERE slug = :slug"),
            {
                "mode": previous_mode,
                "slug": settings.default_tenant_slug,
            },
        )


def test_dashboard_conversation_detail_and_outbox_are_tenant_scoped(app_context):
    engine, tenant_slug = app_context
    client = TestClient(app)
    message_id = f"wamid.DASHBOARD-{uuid4()}"
    payload = _whatsapp_payload(message_id=message_id, text=SAFE_QUESTION)

    result = _process_payload(engine, tenant_slug, payload)

    assert result.status == "PROCESSED"
    assert result.outbound_drafts == 1

    outbound = get_outbound_by_provider_message_id(
        engine=engine,
        tenant_slug=tenant_slug,
        provider_message_id=message_id,
    )
    assert outbound is not None

    foreign_conversation_id = _create_foreign_conversation(engine)
    conversations = client.get("/operator/dashboard/conversations?state=AUTOMATED&limit=200")
    assert conversations.status_code == 200
    conversation_items = conversations.json()
    assert any(item["conversation_id"] == str(outbound.conversation_id) for item in conversation_items)
    assert all(item["conversation_id"] != str(foreign_conversation_id) for item in conversation_items)

    detail = client.get(f"/operator/dashboard/conversations/{outbound.conversation_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["conversation"]["conversation_id"] == str(outbound.conversation_id)
    assert detail_payload["conversation"]["state"] == "AUTOMATED"
    assert detail_payload["messages"]
    assert detail_payload["response_plans"]
    assert detail_payload["verifications"]

    outbox = client.get("/operator/dashboard/outbox")
    assert outbox.status_code == 200
    review_item = next(
        item
        for item in outbox.json()
        if item["outbound_id"] == str(outbound.outbound_id)
    )
    assert review_item["status"] == "PENDING_REVIEW"
    assert review_item["provider_message_id"] is None
    assert review_item["send_attempt_count"] == 0
    assert review_item["customer_message_text"] == SAFE_QUESTION
    assert review_item["verification"]["status"] == "APPROVED"
    assert "KB-002" in review_item["plan"]["knowledge_keys"]
    assert any(
        source["external_key"] == "KB-002"
        for source in review_item["knowledge_sources"]
    )


def _process_payload(engine, tenant_slug: str, payload: dict):
    stored = store_webhook_event(
        engine=engine,
        tenant_slug=tenant_slug,
        provider="whatsapp",
        provider_event_id=f"pytest:{uuid4()}",
        event_kind="message",
        payload=payload,
    )
    return process_whatsapp_webhook(
        engine=engine,
        event_id=stored.event_id,
        tenant_slug=tenant_slug,
        payload=payload,
    )


def _whatsapp_payload(*, message_id: str, text: str) -> dict:
    phone = "54911" + uuid4().hex[:8]
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "pytest-entry",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "5491100000000",
                                "phone_number_id": "pytest-phone-number-id",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Dashboard Pytest"},
                                    "wa_id": phone,
                                }
                            ],
                            "messages": [
                                {
                                    "from": phone,
                                    "id": message_id,
                                    "timestamp": "1781237000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _create_foreign_conversation(engine):
    slug = f"foreign-dashboard-{uuid4()}"
    with engine.begin() as connection:
        tenant_id = connection.execute(
            text(
                """
                INSERT INTO tenants (slug, name, agent_disclosure)
                VALUES (:slug, 'Foreign Dashboard', 'Foreign assistant')
                RETURNING id
                """
            ),
            {"slug": slug},
        ).scalar_one()
        contact_id = connection.execute(
            text(
                """
                INSERT INTO contacts (tenant_id, whatsapp_user_id, display_name)
                VALUES (:tenant_id, :wa_id, 'Foreign Contact')
                RETURNING id
                """
            ),
            {"tenant_id": tenant_id, "wa_id": f"54999{uuid4().hex[:8]}"},
        ).scalar_one()
        return connection.execute(
            text(
                """
                INSERT INTO conversations (tenant_id, contact_id, state)
                VALUES (:tenant_id, :contact_id, 'AUTOMATED')
                RETURNING id
                """
            ),
            {"tenant_id": tenant_id, "contact_id": contact_id},
        ).scalar_one()


def _knowledge_dir(configured_path: str) -> Path:
    configured = Path(configured_path)
    if configured.exists():
        return configured
    return Path(__file__).resolve().parents[3] / "config" / "stoll" / "knowledge"
