from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.database import get_engine
from app.normalization import normalize_whatsapp_messages
from app.repositories.knowledge import import_knowledge_directory, publish_knowledge_item
from app.repositories.messages import persist_inbound_messages_with_context
from app.repositories.outbox import (
    approve_outbound_message,
    get_outbound_by_provider_message_id,
    reject_outbound_message,
)
from app.repositories.webhook_events import store_webhook_event
from app.services.policy_service import evaluate_and_apply_policy
from app.services.response_planner import plan_and_record_response
from app.services.response_verifier import verify_and_record_response
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

    previous_mode = None
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


def test_webhook_pipeline_creates_pending_review_draft_and_approval_is_no_send(
    app_context,
):
    engine, tenant_slug = app_context
    message_id = f"wamid.PYTEST-APPROVE-{uuid4()}"
    payload = _whatsapp_payload(message_id=message_id)

    result = _process_payload(engine, tenant_slug, payload)

    assert result.status == "PROCESSED"
    assert result.normalized_messages == 1
    assert result.response_plans == 1
    assert result.response_verifications == 1
    assert result.rejected_drafts == 0
    assert result.outbound_drafts == 1

    outbound = get_outbound_by_provider_message_id(
        engine=engine,
        tenant_slug=tenant_slug,
        provider_message_id=message_id,
    )
    assert outbound is not None
    assert outbound.status == "PENDING_REVIEW"
    assert outbound.requires_review is True
    assert outbound.provider_message_id is None
    assert _outbound_count(engine, message_id) == 1
    _assert_no_send_attempt(engine, outbound.outbound_id)

    duplicate_result = _process_payload(engine, tenant_slug, payload)

    assert duplicate_result.status == "PROCESSED"
    assert _outbound_count(engine, message_id) == 1

    approved = approve_outbound_message(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound.outbound_id,
        operator_name="pytest",
    )

    assert approved.status == "APPROVED"
    assert approved.approved_by == "pytest"
    assert approved.provider_message_id is None
    assert _outbound_count(engine, message_id) == 1
    _assert_no_send_attempt(engine, approved.outbound_id)


def test_webhook_pipeline_creates_pending_review_draft_and_rejection_is_no_send(
    app_context,
):
    engine, tenant_slug = app_context
    message_id = f"wamid.PYTEST-REJECT-{uuid4()}"
    payload = _whatsapp_payload(message_id=message_id)

    result = _process_payload(engine, tenant_slug, payload)

    assert result.status == "PROCESSED"
    assert result.outbound_drafts == 1

    outbound = get_outbound_by_provider_message_id(
        engine=engine,
        tenant_slug=tenant_slug,
        provider_message_id=message_id,
    )
    assert outbound is not None
    assert outbound.status == "PENDING_REVIEW"

    rejected = reject_outbound_message(
        engine=engine,
        tenant_slug=tenant_slug,
        outbound_id=outbound.outbound_id,
        operator_name="pytest",
        reason="Handled by a human operator in this test.",
    )

    assert rejected.status == "REJECTED"
    assert rejected.rejected_by == "pytest"
    assert rejected.provider_message_id is None
    assert _outbound_count(engine, message_id) == 1
    _assert_no_send_attempt(engine, rejected.outbound_id)


def test_partial_retry_stages_outbox_for_existing_verified_message(app_context):
    engine, tenant_slug = app_context
    message_id = f"wamid.PYTEST-RETRY-{uuid4()}"
    payload = _whatsapp_payload(message_id=message_id)

    [message] = persist_inbound_messages_with_context(
        engine=engine,
        tenant_slug=tenant_slug,
        messages=normalize_whatsapp_messages(payload),
    )
    policy = evaluate_and_apply_policy(
        engine=engine,
        tenant_slug=tenant_slug,
        message=message,
        agent_name="pytest",
    )
    plan = plan_and_record_response(
        engine=engine,
        tenant_slug=tenant_slug,
        message=message,
        policy=policy.decision,
    )
    verification = verify_and_record_response(
        engine=engine,
        tenant_slug=tenant_slug,
        plan=plan,
    )

    assert plan.decision == "ANSWER"
    assert verification.status == "APPROVED"
    assert (
        get_outbound_by_provider_message_id(
            engine=engine,
            tenant_slug=tenant_slug,
            provider_message_id=message_id,
        )
        is None
    )

    result = _process_payload(engine, tenant_slug, payload)

    assert result.status == "PROCESSED"
    assert result.normalized_messages == 1
    outbound = get_outbound_by_provider_message_id(
        engine=engine,
        tenant_slug=tenant_slug,
        provider_message_id=message_id,
    )
    assert outbound is not None
    assert outbound.status == "PENDING_REVIEW"
    assert outbound.provider_message_id is None
    assert _outbound_count(engine, message_id) == 1
    _assert_no_send_attempt(engine, outbound.outbound_id)


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


def _whatsapp_payload(*, message_id: str) -> dict:
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
                                    "profile": {"name": "Outbox Pytest"},
                                    "wa_id": phone,
                                }
                            ],
                            "messages": [
                                {
                                    "from": phone,
                                    "id": message_id,
                                    "timestamp": "1781237000",
                                    "type": "text",
                                    "text": {"body": SAFE_QUESTION},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _knowledge_dir(configured_path: str) -> Path:
    configured = Path(configured_path)
    if configured.exists():
        return configured
    return Path(__file__).resolve().parents[3] / "config" / "stoll" / "knowledge"


def _outbound_count(engine, provider_message_id: str) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM outbound_messages om
                JOIN messages m ON m.id = om.in_reply_to_message_id
                WHERE m.provider_message_id = :provider_message_id
                """
            ),
            {"provider_message_id": provider_message_id},
        ).scalar_one()


def _assert_no_send_attempt(engine, outbound_id) -> None:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT provider_message_id, send_attempt_count, sent_at, failed_at
                FROM outbound_messages
                WHERE id = :outbound_id
                """
            ),
            {"outbound_id": outbound_id},
        ).mappings().one()

    assert row["provider_message_id"] is None
    assert row["send_attempt_count"] == 0
    assert row["sent_at"] is None
    assert row["failed_at"] is None
