import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import get_engine
from app.main import app
from app.repositories.outbox import (
    OutboxTransitionError,
    approve_outbound_message,
    claim_outbound_for_send,
)
from app.repositories.webhook_events import store_webhook_event
from app.services.webhook_processor import process_whatsapp_webhook
from app.services.whatsapp_sender import send_approved_outbound
from app.settings import get_settings
from app.webhooks import extract_whatsapp_event_identity
from app.whatsapp_provider import (
    FakeWhatsAppProvider,
    WhatsAppProviderError,
    WhatsAppProviderPreSendError,
    WhatsAppProviderUncertainError,
)

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


def test_status_webhook_duplicate_is_idempotent(app_context):
    engine, tenant_slug = app_context
    provider_message_id = f"wamid.STATUS-{uuid4()}"
    _create_outbound(
        engine,
        tenant_slug,
        status="SENT",
        provider_message_id=provider_message_id,
    )

    payload = _status_webhook_payload(
        provider_message_id=provider_message_id,
        status="delivered",
        timestamp="1781237100",
    )
    _process_status_payload(engine, tenant_slug, payload)
    _process_status_payload(engine, tenant_slug, payload, expected_status="IGNORED")

    with engine.connect() as connection:
        event_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM outbound_delivery_events
                WHERE provider_message_id = :provider_message_id
                  AND delivery_status = 'DELIVERED'
                """
            ),
            {"provider_message_id": provider_message_id},
        ).scalar_one()
    assert event_count == 1
    _assert_outbound_status(
        engine,
        provider_message_id=provider_message_id,
        status="SENT",
        attempts=0,
        delivery_status="DELIVERED",
    )


def test_status_webhook_transitions_sent_delivered_read(app_context):
    engine, tenant_slug = app_context
    provider_message_id = f"wamid.STATUS-{uuid4()}"
    _create_outbound(
        engine,
        tenant_slug,
        status="SENT",
        provider_message_id=provider_message_id,
        delivery_status="SENT",
    )

    _process_status_payload(
        engine,
        tenant_slug,
        _status_webhook_payload(
            provider_message_id=provider_message_id,
            status="sent",
            timestamp="1781237100",
        ),
    )
    _process_status_payload(
        engine,
        tenant_slug,
        _status_webhook_payload(
            provider_message_id=provider_message_id,
            status="delivered",
            timestamp="1781237200",
        ),
    )
    _process_status_payload(
        engine,
        tenant_slug,
        _status_webhook_payload(
            provider_message_id=provider_message_id,
            status="read",
            timestamp="1781237300",
        ),
    )

    _assert_outbound_status(
        engine,
        provider_message_id=provider_message_id,
        status="SENT",
        attempts=0,
        delivery_status="READ",
    )
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT delivered_at, read_at
                FROM outbound_messages
                WHERE provider_message_id = :provider_message_id
                """
            ),
            {"provider_message_id": provider_message_id},
        ).mappings().one()
    assert row["delivered_at"] is not None
    assert row["read_at"] is not None


def test_late_status_event_does_not_degrade_read_to_sent(app_context):
    engine, tenant_slug = app_context
    provider_message_id = f"wamid.STATUS-{uuid4()}"
    _create_outbound(
        engine,
        tenant_slug,
        status="SENT",
        provider_message_id=provider_message_id,
        delivery_status="READ",
    )

    _process_status_payload(
        engine,
        tenant_slug,
        _status_webhook_payload(
            provider_message_id=provider_message_id,
            status="sent",
            timestamp="1781237400",
        ),
    )

    _assert_outbound_status(
        engine,
        provider_message_id=provider_message_id,
        status="SENT",
        attempts=0,
        delivery_status="READ",
    )


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


def test_concurrent_sends_call_fake_provider_once(app_context):
    engine, tenant_slug = app_context
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")
    fake = FakeWhatsAppProvider(
        provider_message_id="wamid.CONCURRENT",
        delay_seconds=0.2,
    )

    def _send_once():
        return send_approved_outbound(
            engine=engine,
            tenant_slug=tenant_slug,
            outbound_id=outbound_id,
            operator_name="pytest",
            provider=fake,
            send_enabled=True,
            lease_seconds=30,
        )

    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_send_once), pool.submit(_send_once)]
        for future in as_completed(futures):
            try:
                outcomes.append(future.result().status)
            except OutboxTransitionError as exc:
                outcomes.append(str(exc))

    assert len(fake.calls) == 1
    assert "SENT" in outcomes
    assert any("already being sent" in item for item in outcomes)
    _assert_outbound_status(
        engine,
        outbound_id,
        status="SENT",
        attempts=1,
        provider_message_id="wamid.CONCURRENT",
    )


def test_active_send_lease_cannot_be_stolen(app_context):
    engine, tenant_slug = app_context
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")

    first = claim_outbound_for_send(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound_id,
        operator_name="pytest",
        lease_owner="first-lease",
        lease_seconds=60,
    )

    assert first.status == "QUEUED"
    assert first.lease_owner == "first-lease"
    with pytest.raises(OutboxTransitionError, match="already being sent"):
        claim_outbound_for_send(
            engine=engine,
            tenant_slug=tenant_slug,
            outbound_id=outbound_id,
            operator_name="pytest",
            lease_owner="second-lease",
            lease_seconds=60,
        )
    _assert_outbound_status(engine, outbound_id, status="QUEUED", attempts=1)


def test_expired_send_lease_can_be_recovered(app_context):
    engine, tenant_slug = app_context
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")
    claim_outbound_for_send(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound_id,
        operator_name="pytest",
        lease_owner="expired-lease",
        lease_seconds=60,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE outbound_messages
                SET lease_expires_at = now() - interval '1 second'
                WHERE id = :outbound_id
                """
            ),
            {"outbound_id": outbound_id},
        )

    recovered = claim_outbound_for_send(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound_id,
        operator_name="pytest",
        lease_owner="recovered-lease",
        lease_seconds=60,
    )

    assert recovered.status == "QUEUED"
    assert recovered.lease_owner == "recovered-lease"
    _assert_outbound_status(engine, outbound_id, status="QUEUED", attempts=2)


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
        error=WhatsAppProviderPreSendError(
            "simulated provider failure",
            error_type="PRE_SEND_FAILURE",
            retryable=True,
        ),
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


def test_accepted_request_timeout_marks_unknown(app_context, monkeypatch):
    engine, tenant_slug = app_context
    client = _logged_client(engine, tenant_slug, role="OPERATOR")
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")
    fake = _enable_sending(
        monkeypatch,
        error=WhatsAppProviderUncertainError(
            "accepted by provider and connection timed out",
            error_type="REQUEST_TIMEOUT_UNKNOWN",
            request_started=True,
            provider_message_id="wamid.UNKNOWN",
            latency_ms=123,
        ),
    )

    response = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "UNKNOWN"
    assert response.json()["provider_message_id"] == "wamid.UNKNOWN"
    assert len(fake.calls) == 1
    _assert_outbound_status(
        engine,
        outbound_id,
        status="UNKNOWN",
        attempts=1,
        provider_message_id="wamid.UNKNOWN",
    )
    _assert_last_attempt(
        engine,
        outbound_id,
        status="UNKNOWN",
        error_type="REQUEST_TIMEOUT_UNKNOWN",
        provider_message_id="wamid.UNKNOWN",
    )


def test_unknown_outbound_is_not_retried_automatically(app_context, monkeypatch):
    engine, tenant_slug = app_context
    client = _logged_client(engine, tenant_slug, role="OPERATOR")
    outbound_id = _create_outbound(
        engine,
        tenant_slug,
        status="UNKNOWN",
        provider_message_id="wamid.UNKNOWN-NO-RETRY",
    )
    fake = _enable_sending(monkeypatch, provider_message_id="wamid.SHOULD-NOT-SEND")

    response = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )

    assert response.status_code == 409
    assert "unknown send result" in response.json()["detail"]
    assert fake.calls == []
    _assert_outbound_status(
        engine,
        outbound_id,
        status="UNKNOWN",
        attempts=0,
        provider_message_id="wamid.UNKNOWN-NO-RETRY",
    )


def test_meta_error_is_recorded_without_exposing_secrets(app_context, monkeypatch):
    engine, tenant_slug = app_context
    client = _logged_client(engine, tenant_slug, role="ADMIN")
    outbound_id = _create_outbound(engine, tenant_slug, status="APPROVED")
    fake = _enable_sending(
        monkeypatch,
        error=WhatsAppProviderError(
            "Meta rejected request access_token=super-secret-token",
            error_type="META_HTTP_ERROR",
            status_code=400,
            request_started=True,
            latency_ms=9,
        ),
    )

    response = client.post(
        f"/operator/outbox/{outbound_id}/send",
        headers=csrf_headers(client),
        json={},
    )

    assert response.status_code == 502
    assert len(fake.calls) == 1
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT error_message
                FROM outbound_messages
                WHERE id = :outbound_id
                """
            ),
            {"outbound_id": outbound_id},
        ).mappings().one()
    assert "super-secret-token" not in row["error_message"]
    assert "access_token=[redacted]" in row["error_message"]


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


def test_whatsapp_send_feature_flag_defaults_to_false():
    assert get_settings().whatsapp_send_enabled is False


def _enable_sending(
    monkeypatch,
    *,
    provider_message_id: str = "wamid.FAKE",
    error: WhatsAppProviderError | None = None,
    delay_seconds: float = 0.0,
) -> FakeWhatsAppProvider:
    settings = get_settings().model_copy(update={"whatsapp_send_enabled": True})
    app.dependency_overrides[get_settings] = lambda: settings
    fake = FakeWhatsAppProvider(
        provider_message_id=provider_message_id,
        error=error,
        delay_seconds=delay_seconds,
    )
    monkeypatch.setattr(outbox_router, "get_whatsapp_provider", lambda _: fake)
    return fake


def _logged_client(engine, tenant_slug: str, *, role: str) -> TestClient:
    client = TestClient(app)
    email = f"send-{role.lower()}-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=email, role=role)
    assert login(client, email=email, tenant_slug=tenant_slug).status_code == 200
    return client


def _create_outbound(
    engine,
    tenant_slug: str,
    *,
    status: str,
    provider_message_id: str | None = None,
    delivery_status: str | None = None,
):
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
                    provider_message_id,
                    delivery_status,
                    sent_at,
                    unknown_at,
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
                    :provider_message_id,
                    :delivery_status,
                    CASE WHEN :status = 'SENT' THEN now() ELSE NULL END,
                    CASE WHEN :status = 'UNKNOWN' THEN now() ELSE NULL END,
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
                "provider_message_id": provider_message_id,
                "delivery_status": delivery_status,
            },
        ).scalar_one()


def _assert_outbound_status(
    engine,
    outbound_id=None,
    *,
    provider_message_id: str | None = None,
    status: str,
    attempts: int,
    expected_provider_message_id: str | None = None,
    delivery_status: str | None = None,
) -> None:
    if outbound_id is None and provider_message_id is None:
        raise AssertionError("outbound_id or provider_message_id is required")
    where_clause = "id = :lookup" if outbound_id is not None else "provider_message_id = :lookup"
    lookup = outbound_id if outbound_id is not None else provider_message_id
    with engine.connect() as connection:
        row = connection.execute(
            text(
                f"""
                SELECT
                    status,
                    provider_message_id,
                    send_attempt_count,
                    sent_at,
                    failed_at,
                    unknown_at,
                    delivery_status,
                    lease_owner,
                    lease_expires_at
                FROM outbound_messages
                WHERE {where_clause}
                """
            ),
            {"lookup": lookup},
        ).mappings().one()

    assert row["status"] == status
    if expected_provider_message_id is not None:
        assert row["provider_message_id"] == expected_provider_message_id
    elif outbound_id is not None:
        assert row["provider_message_id"] == provider_message_id
    assert row["send_attempt_count"] == attempts
    if delivery_status is not None:
        assert row["delivery_status"] == delivery_status
    if status == "SENT":
        assert row["sent_at"] is not None
        assert row["failed_at"] is None
    elif status == "FAILED":
        assert row["sent_at"] is None
        assert row["failed_at"] is not None
        assert row["lease_owner"] is None
        assert row["lease_expires_at"] is None
    elif status == "UNKNOWN":
        assert row["unknown_at"] is not None
        assert row["lease_owner"] is None
        assert row["lease_expires_at"] is None
    else:
        assert row["sent_at"] is None


def _assert_last_attempt(
    engine,
    outbound_id,
    *,
    status: str,
    error_type: str | None = None,
    provider_message_id: str | None = None,
) -> None:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT status, error_type, provider_message_id, completed_at
                FROM outbound_send_attempts
                WHERE outbound_message_id = :outbound_id
                ORDER BY attempt_number DESC
                LIMIT 1
                """
            ),
            {"outbound_id": outbound_id},
        ).mappings().one()

    assert row["status"] == status
    assert row["error_type"] == error_type
    assert row["provider_message_id"] == provider_message_id
    assert row["completed_at"] is not None


def _process_status_payload(
    engine,
    tenant_slug: str,
    payload: dict,
    *,
    expected_status: str = "PROCESSED",
) -> None:
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    provider_event_id, event_kind = extract_whatsapp_event_identity(payload, raw_body)
    stored = store_webhook_event(
        engine=engine,
        tenant_slug=tenant_slug,
        provider="whatsapp",
        provider_event_id=provider_event_id,
        event_kind=event_kind,
        payload=payload,
    )
    result = process_whatsapp_webhook(
        engine=engine,
        event_id=stored.event_id,
        tenant_slug=tenant_slug,
        payload=payload,
        llm_drafting_enabled=False,
    )
    assert result.status == expected_status


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


def _status_webhook_payload(
    *,
    provider_message_id: str,
    status: str,
    timestamp: str,
    error_code: str | None = None,
) -> dict:
    status_payload: dict = {
        "id": provider_message_id,
        "status": status,
        "timestamp": timestamp,
        "recipient_id": "5491112345678",
    }
    if error_code is not None:
        status_payload["errors"] = [
            {
                "code": error_code,
                "title": "Meta delivery error",
                "message": "Delivery failed",
            }
        ]
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
                            "statuses": [status_payload],
                        },
                    }
                ],
            }
        ],
    }
