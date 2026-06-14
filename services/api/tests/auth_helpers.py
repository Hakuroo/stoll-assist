from uuid import UUID

from fastapi.testclient import TestClient

from app.auth import create_or_update_tenant_user, get_active_tenant_id_by_slug

TEST_PASSWORD = "CorrectHorse123!"
ORIGIN = "http://localhost:3000"


def seed_user(
    engine,
    *,
    tenant_slug: str,
    email: str,
    role: str,
    password: str = TEST_PASSWORD,
    display_name: str | None = None,
):
    tenant_id = get_active_tenant_id_by_slug(engine=engine, tenant_slug=tenant_slug)
    return create_or_update_tenant_user(
        engine=engine,
        tenant_id=tenant_id,
        email=email,
        display_name=display_name or role.title(),
        password=password,
        role=role,
        active=True,
    )


def login(
    client: TestClient,
    *,
    email: str,
    password: str = TEST_PASSWORD,
    tenant_slug: str | None = None,
):
    body = {"email": email, "password": password}
    if tenant_slug is not None:
        body["tenant_slug"] = tenant_slug
    return client.post("/auth/login", json=body)


def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("stoll_assist_csrf")
    assert token
    return {"origin": ORIGIN, "x-csrf-token": token}


def tenant_id_for(engine, tenant_slug: str) -> UUID:
    return get_active_tenant_id_by_slug(engine=engine, tenant_slug=tenant_slug)
