import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import get_engine
from app.main import app
from app.repositories.outbox import approve_outbound_message, claim_outbound_for_send
from app.settings import get_settings
from app.whatsapp_provider import FakeWhatsAppProvider, WhatsAppProviderError

import app.routers.outbox as outbox_router
from auth_helpers import csrf_headers, login, seed_user


@pytest.fixture()
def app_context():
    try:
        settings = get_settings()
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Integration database is not available: {exc}")
    return engine, settings.default_tenant_slug


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_whatsapp_webhook_verification_get_accepts_valid_token_and_rejects_invalid():
    client = TestClient(app)

    accepted = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-token",
            "hub.challenge": "challenge-123",
        },
    )
    rejected = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "challenge-123",
        },
    )

    assert accepted.status_code == 200
    assert accepted.text == "challenge-123"
    assert rejected.status_code == 403


def test_whatsapp_webhook_post_validates_signature_and_deduplicates(app_context):
    payload = _webhook_payload(message_id=f"wamid.WEBHOOK-{uuid4()}")
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    client = TestClient(app)

    invalid = client.post(
        "/webhooks/whatsapp",
        content=raw_body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": "sha256=bad",
        },
    )
    first = client.post(
        "/webhooks/whatsapp",
        content=raw_body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": _signature(raw_body),
        },
    )
    second = client.post(
        "/webhooks/whatsapp",
        content=raw_body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": _signature(raw_body),
        },
    )

    assert invalid.status_code == 401
    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["event_id"] == first.json()["event_id"]


def test_send_endpoint_blocks_when_feature_flag_is_disabled(app_context):
    engine, tenant_slug = app_context
    client = _logged_client(engine, tenant_slug, role="OPERATOR")
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")

    response = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )

    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]
    _assert_outbound_status(engine, outbound_id, status="APPROVED", attempts=0)


def test_viewer_cannot_send(app_context, monkeypatch):
    engine, tenant_slug = app_context
    client = _logged_client(engine, tenant_slug, role="VIEWER")
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")
    fake = _enable_sending(monkeypatch)

    response = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )

    assert response.status_code == 403
    assert fake.calls == []


def test_non_approved_outbound_cannot_be_sent(app_context, monkeypatch):
    engine, tenant_slug = app_context
    client = _logged_client(engine, tenant_slug, role="OPERATOR")
    outbound_id = _create_outbound(engine, tenant_slug, status="PENDING_REVIEW")
    fake = _enable_sending(monkeypatch)

    response = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )

    assert response.status_code == 409
    assert fake.calls == []
    _assert_outbound_status(engine, outbound_id, status="PENDING_REVIEW", attempts=0)


def test_send_success_uses_fake_provider_once_and_persists_provider_message_id(
    app_context,
    monkeypatch,
):
    engine, tenant_slug = app_context
    client = _logged_client(engine, tenant_slug, role="OPERATOR")
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")
    fake = _enable_sending(monkeypatch, provider_message_id="wamid.FAKE-SENT")

    first = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )
    second = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )

    assert first.status_code == 200
    assert first.json()["status"] == "SENT"
    assert first.json()["provider_message_id"] == "wamid.FAKE-SENT"
    assert second.status_code == 200
    assert second.json()["status"] == "SENT"
    assert len(fake.calls) == 1
    _assert_outbound_status(
        engine,
        outbound_id,
        status="SENT",
        attempts=1,
        provider_message_id="wamid.FAKE-SENT",
    )


def test_in_flight_retry_does_not_call_provider_again(app_context, monkeypatch):
    engine, tenant_slug = app_context
    client = _logged_client(engine, tenant_slug, role="OPERATOR")
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")
    claim_outbound_for_send(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound_id,
        operator_name="pytest",
    )
    fake = _enable_sending(monkeypatch)

    response = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )

    assert response.status_code == 409
    assert fake.calls == []
    _assert_outbound_status(engine, outbound_id, status="QUEUED", attempts=1)


def test_send_error_marks_failed_without_success_metadata(app_context, monkeypatch):
    engine, tenant_slug = app_context
    client = _logged_client(engine, tenant_slug, role="ADMIN")
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")
    fake = _enable_sending(
        monkeypatch,
        error=WhatsAppProviderError("simulated provider failure", retryable=True),
    )

    response = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "WhatsApp provider send failed"
    assert len(fake.calls) == 1
    _assert_outbound_status(engine, outbound_id, status="FAILED", attempts=1)


def test_approval_still_does_not_send(app_context, monkeypatch):
    engine, tenant_slug = app_context
    outbound_id = _create_outbound(engine, tenant_slug, status="PENDING_REVIEW")
    fake = _enable_sending(monkeypatch)

    approved = approve_outbound_message(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound_id,
        operator_name="pytest",
    )

    assert approved.status == "APPROVED"
    assert approved.provider_message_id is None
    assert approved.send_attempt_count == 0
    assert fake.calls == []


def _enable_sending(
    monkeypatch,
    *,
    provider_message_id: str = "wamid.FAKE",
    error: WhatsAppProviderError | None = None,
) -> FakeWhatsAppProvider:
    settings = get_settings().model_copy(update={"whatsapp_send_enabled": True})
    app.dependency_overrides[get_settings] = lambda: settings
    fake = FakeWhatsAppProvider(provider_message_id=provider_message_id, error=error)
    monkeypatch.setattr(outbox_router, "get_whatsapp_provider", lambda _: fake)
    return fake


def _logged_client(engine, tenant_slug: str, *, role: str) -> TestClient:
    client = TestClient(app)
    email = f"send-{role.lower()}-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=email, role=role)
    assert login(client, email=email, tenant_slug=tenant_slug).status_code == 200
    return client


def _create_outbound(engine, tenant_slug: str, *, status: str):
    with engine.begin() as connection:
        tenant_id = connection.execute(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": tenant_slug},
        ).scalar_one()
        contact_id = connection.execute(
            text(
                """
                INSERT INTO contacts (tenant_id, whatsapp_user_id, display_name)
                VALUES (:tenant_id, :wa_id, 'Send Test')
                RETURNING id
                """
            ),
            {"tenant_id": tenant_id, "wa_id": f"54911{uuid4().hex[:8]}"},
        ).scalar_one()
        conversation_id = connection.execute(
            text(
                """
                INSERT INTO conversations (tenant_id, contact_id, state)
                VALUES (:tenant_id, :contact_id, 'AUTOMATED')
                RETURNING id
                """
            ),
            {"tenant_id": tenant_id, "contact_id": contact_id},
        ).scalar_one()
        message_id = connection.execute(
            text(
                """
                INSERT INTO messages (
                    tenant_id,
                    conversation_id,
                    provider_message_id,
                    direction,
                    message_type,
                    body_text,
                    raw_payload
                )
                VALUES (
                    :tenant_id,
                    :conversation_id,
                    :provider_message_id,
                    'INBOUND',
                    'text',
                    'Necesito informacion',
                    '{}'::jsonb
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "provider_message_id": f"wamid.IN-{uuid4()}",
            },
        ).scalar_one()
        plan_id = connection.execute(
            text(
                """
                INSERT INTO response_plans (
                    tenant_id,
                    conversation_id,
                    message_id,
                    decision,
                    reason_code,
                    reply_goal,
                    draft_reply
                )
                VALUES (
                    :tenant_id,
                    :conversation_id,
                    :message_id,
                    'ANSWER',
                    'safe-answer',
                    'Responder',
                    'Hola, podemos ayudarte con la consulta.'
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
            },
        ).scalar_one()
        verification_id = connection.execute(
            text(
                """
                INSERT INTO response_verifications (
                    tenant_id,
                    plan_id,
                    conversation_id,
                    message_id,
                    status,
                    reason_code
                )
                VALUES (
                    :tenant_id,
                    :plan_id,
                    :conversation_id,
                    :message_id,
                    'APPROVED',
                    'safe'
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
            },
        ).scalar_one()
        return connection.execute(
            text(
                """
                INSERT INTO outbound_messages (
                    tenant_id,
                    conversation_id,
                    in_reply_to_message_id,
                    plan_id,
                    verification_id,
                    recipient,
                    body_text,
                    body_sha256,
                    status,
                    requires_review,
                    approved_by,
                    approved_at
                )
                VALUES (
                    :tenant_id,
                    :conversation_id,
                    :message_id,
                    :plan_id,
                    :verification_id,
                    '5491112345678',
                    'Hola, podemos ayudarte con la consulta.',
                    :body_sha256,
                    :status,
                    true,
                    CASE WHEN :status = 'APPROVED' THEN 'pytest' ELSE NULL END,
                    CASE WHEN :status = 'APPROVED' THEN now() ELSE NULL END
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "plan_id": plan_id,
                "verification_id": verification_id,
                "body_sha256": hashlib.sha256(
                    "Hola, podemos ayudarte con la consulta.".encode("utf-8")
                ).hexdigest(),
                "status": status,
            },
        ).scalar_one()


def _assert_outbound_status(
    engine,
    outbound_id,
    *,
    status: str,
    attempts: int,
    provider_message_id: str | None = None,
) -> None:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT status, provider_message_id, send_attempt_count, sent_at, failed_at
                FROM outbound_messages
                WHERE id = :outbound_id
                """
            ),
            {"outbound_id": outbound_id},
        ).mappings().one()

    assert row["status"] == status
    assert row["provider_message_id"] == provider_message_id
    assert row["send_attempt_count"] == attempts
    if status == "SENT":
        assert row["sent_at"] is not None
        assert row["failed_at"] is None
    elif status == "FAILED":
        assert row["sent_at"] is None
        assert row["failed_at"] is not None
    else:
        assert row["sent_at"] is None


def _signature(raw_body: bytes) -> str:
    return "sha256=" + hmac.new(b"replace-me", raw_body, hashlib.sha256).hexdigest()


def _webhook_payload(*, message_id: str) -> dict:
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
                                    "profile": {"name": "Webhook Pytest"},
                                    "wa_id": "5491112345678",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "5491112345678",
                                    "id": message_id,
                                    "timestamp": "1781237000",
                                    "type": "text",
                                    "text": {"body": "Hola"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
