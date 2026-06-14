import getpass
import os
import sys
from uuid import UUID

from sqlalchemy import text

from app.auth import (
    ROLE_OWNER,
    create_or_update_tenant_user,
    get_active_tenant_id_by_slug,
    normalize_email,
)
from app.database import get_engine
from app.settings import get_settings


def main() -> None:
    settings = get_settings()
    engine = get_engine()
    tenant_slug = _read_value("OWNER_TENANT_SLUG", settings.default_tenant_slug)
    email = normalize_email(_read_value("OWNER_EMAIL"))
    display_name = _read_value("OWNER_DISPLAY_NAME", "Owner local")
    password = _read_password()
    tenant_id = get_active_tenant_id_by_slug(engine=engine, tenant_slug=tenant_slug)

    existing_owners = _active_owner_emails(tenant_id)
    if existing_owners and email not in existing_owners:
        raise SystemExit(
            f"Tenant {tenant_slug} already has an active OWNER. "
            "Use the admin API from an authenticated OWNER/ADMIN session."
        )

    user = create_or_update_tenant_user(
        engine=engine,
        tenant_id=tenant_id,
        email=email,
        display_name=display_name,
        password=password,
        role=ROLE_OWNER,
        active=True,
    )
    print(f"OWNER ready for tenant {tenant_slug}: {user.email}")


def _read_value(env_name: str, default: str | None = None) -> str:
    value = os.getenv(env_name)
    if value:
        return value
    if default is not None:
        if not sys.stdin.isatty():
            return default
        prompt = f"{env_name} [{default}]: "
        typed = input(prompt).strip()
        return typed or default
    if not sys.stdin.isatty():
        raise SystemExit(f"{env_name} is required")
    return input(f"{env_name}: ").strip()


def _read_password() -> str:
    value = os.getenv("OWNER_PASSWORD")
    if value:
        return value
    if not sys.stdin.isatty():
        raise SystemExit("OWNER_PASSWORD is required")
    return getpass.getpass("OWNER_PASSWORD: ")


def _active_owner_emails(tenant_id: UUID) -> set[str]:
    engine = get_engine()
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT u.email
                FROM tenant_memberships tm
                JOIN users u ON u.id = tm.user_id
                WHERE tm.tenant_id = :tenant_id
                  AND tm.role = 'OWNER'
                  AND tm.active = true
                  AND u.status = 'active'
                """
            ),
            {"tenant_id": tenant_id},
        ).scalars().all()
    return set(rows)


if __name__ == "__main__":
    main()
