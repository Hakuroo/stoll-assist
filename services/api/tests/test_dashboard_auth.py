from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import hash_token
from app.database import get_engine
from app.main import app
from app.settings import get_settings

from auth_helpers import TEST_PASSWORD, csrf_headers, login, seed_user


@pytest.fixture()
def auth_context():
    try:
        settings = get_settings()
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Integration database is not available: {exc}")
    return engine, settings.default_tenant_slug


def test_login_creates_session_cookie_and_does_not_store_raw_token(auth_context):
    engine, tenant_slug = auth_context
    email = f"owner-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=email, role="OWNER")
    client = TestClient(app)

    response = login(client, email=email, tenant_slug=tenant_slug)

    assert response.status_code == 200
    assert response.json()["role"] == "OWNER"
    raw_token = client.cookies.get("stoll_assist_session")
    csrf_token = client.cookies.get("stoll_assist_csrf")
    assert raw_token
    assert csrf_token

    set_cookie_headers = response.headers.get_list("set-cookie")
    session_cookie = next(value for value in set_cookie_headers if "stoll_assist_session=" in value)
    csrf_cookie = next(value for value in set_cookie_headers if "stoll_assist_csrf=" in value)
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Path=/" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "SameSite=lax" in csrf_cookie

    with engine.connect() as connection:
        raw_match = connection.execute(
            text("SELECT COUNT(*) FROM auth_sessions WHERE token_hash = :token"),
            {"token": raw_token},
        ).scalar_one()
        hashed_match = connection.execute(
            text("SELECT COUNT(*) FROM auth_sessions WHERE token_hash = :token_hash"),
            {"token_hash": hash_token(raw_token)},
        ).scalar_one()
    assert raw_match == 0
    assert hashed_match == 1


def test_wrong_password_does_not_reveal_whether_user_exists(auth_context):
    engine, tenant_slug = auth_context
    email = f"operator-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=email, role="OPERATOR")
    client = TestClient(app)

    known = login(client, email=email, password="wrong-password", tenant_slug=tenant_slug)
    unknown = login(client, email=f"missing-{uuid4()}@example.com", password="wrong-password", tenant_slug=tenant_slug)

    assert known.status_code == 401
    assert unknown.status_code == 401
    assert known.json() == unknown.json()


def test_operator_endpoint_without_session_returns_401(auth_context):
    client = TestClient(app)
    response = client.get("/operator/dashboard/conversations")
    assert response.status_code == 401


def test_expired_and_revoked_sessions_return_401(auth_context):
    engine, tenant_slug = auth_context
    email = f"viewer-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=email, role="VIEWER")
    client = TestClient(app)
    assert login(client, email=email, tenant_slug=tenant_slug).status_code == 200
    token = client.cookies.get("stoll_assist_session")
    assert token

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE auth_sessions SET expires_at = now() - interval '1 minute' WHERE token_hash = :token_hash"),
            {"token_hash": hash_token(token)},
        )
    assert client.get("/auth/me").status_code == 401

    client = TestClient(app)
    assert login(client, email=email, tenant_slug=tenant_slug).status_code == 200
    token = client.cookies.get("stoll_assist_session")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE auth_sessions SET revoked_at = now() WHERE token_hash = :token_hash"),
            {"token_hash": hash_token(token or "")},
        )
    assert client.get("/auth/me").status_code == 401


def test_user_from_tenant_a_cannot_access_tenant_b(auth_context):
    engine, tenant_slug = auth_context
    email = f"tenant-a-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=email, role="OPERATOR")
    foreign_conversation_id = _create_foreign_conversation(engine)
    client = TestClient(app)
    assert login(client, email=email, tenant_slug=tenant_slug).status_code == 200

    response = client.get(f"/operator/dashboard/conversations/{foreign_conversation_id}")

    assert response.status_code == 404


def test_viewer_and_operator_role_limits(auth_context):
    engine, tenant_slug = auth_context
    viewer_email = f"viewer-role-{uuid4()}@example.com"
    operator_email = f"operator-role-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=viewer_email, role="VIEWER")
    seed_user(engine, tenant_slug=tenant_slug, email=operator_email, role="OPERATOR")

    viewer = TestClient(app)
    assert login(viewer, email=viewer_email, tenant_slug=tenant_slug).status_code == 200
    assert viewer.get("/operator/dashboard/conversations").status_code == 200
    viewer_mutation = viewer.post(
        "/operator/knowledge/import-config",
        headers=csrf_headers(viewer),
    )
    assert viewer_mutation.status_code == 403

    operator = TestClient(app)
    assert login(operator, email=operator_email, tenant_slug=tenant_slug).status_code == 200
    assert operator.get("/operator/users").status_code == 403
    operator_knowledge = operator.post(
        "/operator/knowledge/import-config",
        headers=csrf_headers(operator),
    )
    assert operator_knowledge.status_code == 403


def test_admin_and_owner_permissions(auth_context):
    engine, tenant_slug = auth_context
    admin_email = f"admin-{uuid4()}@example.com"
    owner_email = f"owner-perm-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=admin_email, role="ADMIN")
    seed_user(engine, tenant_slug=tenant_slug, email=owner_email, role="OWNER")

    admin = TestClient(app)
    assert login(admin, email=admin_email, tenant_slug=tenant_slug).status_code == 200
    assert admin.get("/operator/users").status_code == 200
    created = admin.post(
        "/operator/users",
        headers=csrf_headers(admin),
        json={
            "email": f"created-{uuid4()}@example.com",
            "display_name": "Created User",
            "password": TEST_PASSWORD,
            "role": "VIEWER",
        },
    )
    assert created.status_code == 200
    assert created.json()["role"] == "VIEWER"

    owner = TestClient(app)
    assert login(owner, email=owner_email, tenant_slug=tenant_slug).status_code == 200
    assert owner.get("/operator/users").status_code == 200


def test_logout_and_logout_all_invalidate_sessions(auth_context):
    engine, tenant_slug = auth_context
    email = f"logout-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=email, role="ADMIN")

    client = TestClient(app)
    assert login(client, email=email, tenant_slug=tenant_slug).status_code == 200
    logout = client.post("/auth/logout", headers=csrf_headers(client))
    assert logout.status_code == 200
    assert client.get("/auth/me").status_code == 401

    first = TestClient(app)
    second = TestClient(app)
    assert login(first, email=email, tenant_slug=tenant_slug).status_code == 200
    assert login(second, email=email, tenant_slug=tenant_slug).status_code == 200
    logout_all = first.post("/auth/logout-all", headers=csrf_headers(first))
    assert logout_all.status_code == 200
    assert first.get("/auth/me").status_code == 401
    assert second.get("/auth/me").status_code == 401


def test_csrf_blocks_invalid_mutation(auth_context):
    engine, tenant_slug = auth_context
    email = f"csrf-{uuid4()}@example.com"
    seed_user(engine, tenant_slug=tenant_slug, email=email, role="ADMIN")
    client = TestClient(app)
    assert login(client, email=email, tenant_slug=tenant_slug).status_code == 200

    missing = client.post("/operator/knowledge/import-config")
    wrong = client.post(
        "/operator/knowledge/import-config",
        headers={"origin": "http://localhost:3000", "x-csrf-token": "wrong"},
    )
    bad_origin = client.post(
        "/operator/knowledge/import-config",
        headers={"origin": "http://evil.example", "x-csrf-token": client.cookies.get("stoll_assist_csrf") or ""},
    )

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert bad_origin.status_code == 403


def _create_foreign_conversation(engine):
    slug = f"foreign-auth-{uuid4()}"
    with engine.begin() as connection:
        tenant_id = connection.execute(
            text(
                """
                INSERT INTO tenants (slug, name, agent_disclosure)
                VALUES (:slug, 'Foreign Auth', 'Foreign assistant')
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
