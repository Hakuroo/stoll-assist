import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from sqlalchemy import Engine, text

from app.database import get_engine
from app.settings import Settings, get_settings


ROLE_OWNER = "OWNER"
ROLE_ADMIN = "ADMIN"
ROLE_OPERATOR = "OPERATOR"
ROLE_VIEWER = "VIEWER"

READ_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER})
OPERATE_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN, ROLE_OPERATOR})
ADMIN_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN})

_password_hash = PasswordHash.recommended()
_dummy_password_hash = _password_hash.hash("not-a-real-dashboard-password")


@dataclass(frozen=True)
class AuthContext:
    session_id: UUID
    user_id: UUID
    tenant_id: UUID
    tenant_slug: str
    tenant_name: str
    email: str
    display_name: str
    role: str
    expires_at: datetime


@dataclass(frozen=True)
class CreatedSession:
    context: AuthContext
    token: str
    csrf_token: str


@dataclass(frozen=True)
class OperatorUser:
    user_id: UUID
    email: str
    display_name: str
    status: str
    role: str
    membership_active: bool
    last_login_at: datetime | None
    created_at: datetime


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("Invalid email")
    return normalized


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash or _dummy_password_hash
    return _password_hash.verify(password, candidate_hash)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()


def authenticate_and_create_session(
    *,
    engine: Engine,
    settings: Settings,
    email: str,
    password: str,
    tenant_slug: str,
    user_agent: str | None,
) -> CreatedSession | None:
    normalized_email = normalize_email(email)
    with engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                    u.id AS user_id,
                    u.email,
                    u.display_name,
                    u.password_hash,
                    u.status,
                    t.id AS tenant_id,
                    t.slug AS tenant_slug,
                    t.name AS tenant_name,
                    t.status AS tenant_status,
                    tm.role,
                    tm.active AS membership_active
                FROM users u
                JOIN tenant_memberships tm ON tm.user_id = u.id
                JOIN tenants t ON t.id = tm.tenant_id
                WHERE u.email = :email
                  AND t.slug = :tenant_slug
                LIMIT 1
                """
            ),
            {"email": normalized_email, "tenant_slug": tenant_slug},
        ).mappings().one_or_none()

        password_ok = verify_password(password, None if row is None else row["password_hash"])
        if (
            row is None
            or not password_ok
            or row["status"] != "active"
            or row["tenant_status"] != "active"
            or not row["membership_active"]
        ):
            return None

        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.auth_session_ttl_minutes)
        inserted = connection.execute(
            text(
                """
                INSERT INTO auth_sessions (
                    user_id,
                    tenant_id,
                    token_hash,
                    csrf_token_hash,
                    expires_at,
                    user_agent_hash
                )
                VALUES (
                    :user_id,
                    :tenant_id,
                    :token_hash,
                    :csrf_token_hash,
                    :expires_at,
                    :user_agent_hash
                )
                RETURNING id
                """
            ),
            {
                "user_id": row["user_id"],
                "tenant_id": row["tenant_id"],
                "token_hash": hash_token(token),
                "csrf_token_hash": hash_token(csrf_token),
                "expires_at": expires_at,
                "user_agent_hash": hash_user_agent(user_agent),
            },
        ).mappings().one()
        connection.execute(
            text("UPDATE users SET last_login_at = now(), updated_at = now() WHERE id = :user_id"),
            {"user_id": row["user_id"]},
        )

        context = AuthContext(
            session_id=inserted["id"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            tenant_slug=row["tenant_slug"],
            tenant_name=row["tenant_name"],
            email=row["email"],
            display_name=row["display_name"],
            role=row["role"],
            expires_at=expires_at,
        )
        return CreatedSession(context=context, token=token, csrf_token=csrf_token)


def load_session_context(*, engine: Engine, token: str | None) -> AuthContext | None:
    if not token:
        return None
    token_hash = hash_token(token)
    with engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                    s.id AS session_id,
                    s.user_id,
                    s.tenant_id,
                    s.expires_at,
                    u.email,
                    u.display_name,
                    t.slug AS tenant_slug,
                    t.name AS tenant_name,
                    tm.role
                FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                JOIN tenants t ON t.id = s.tenant_id
                JOIN tenant_memberships tm
                  ON tm.user_id = s.user_id
                 AND tm.tenant_id = s.tenant_id
                WHERE s.token_hash = :token_hash
                  AND s.revoked_at IS NULL
                  AND s.expires_at > now()
                  AND u.status = 'active'
                  AND t.status = 'active'
                  AND tm.active = true
                LIMIT 1
                """
            ),
            {"token_hash": token_hash},
        ).mappings().one_or_none()
        if row is None:
            return None
        connection.execute(
            text("UPDATE auth_sessions SET last_seen_at = now() WHERE id = :session_id"),
            {"session_id": row["session_id"]},
        )
        return AuthContext(
            session_id=row["session_id"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            tenant_slug=row["tenant_slug"],
            tenant_name=row["tenant_name"],
            email=row["email"],
            display_name=row["display_name"],
            role=row["role"],
            expires_at=row["expires_at"],
        )


def revoke_session(*, engine: Engine, session_id: UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE auth_sessions SET revoked_at = now() WHERE id = :session_id"),
            {"session_id": session_id},
        )


def revoke_all_sessions(*, engine: Engine, user_id: UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE auth_sessions
                SET revoked_at = now()
                WHERE user_id = :user_id
                  AND revoked_at IS NULL
                """
            ),
            {"user_id": user_id},
        )


def get_active_tenant_id_by_slug(*, engine: Engine, tenant_slug: str) -> UUID:
    with engine.connect() as connection:
        tenant_id = connection.execute(
            text("SELECT id FROM tenants WHERE slug = :slug AND status = 'active'"),
            {"slug": tenant_slug},
        ).scalar_one_or_none()
    if tenant_id is None:
        raise LookupError(f"Active tenant not found: {tenant_slug}")
    return tenant_id


def list_tenant_users(*, engine: Engine, tenant_id: UUID) -> list[OperatorUser]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT
                    u.id AS user_id,
                    u.email,
                    u.display_name,
                    u.status,
                    tm.role,
                    tm.active AS membership_active,
                    u.last_login_at,
                    u.created_at
                FROM tenant_memberships tm
                JOIN users u ON u.id = tm.user_id
                WHERE tm.tenant_id = :tenant_id
                ORDER BY u.email
                """
            ),
            {"tenant_id": tenant_id},
        ).mappings().all()
    return [
        OperatorUser(
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            status=row["status"],
            role=row["role"],
            membership_active=row["membership_active"],
            last_login_at=row["last_login_at"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def create_or_update_tenant_user(
    *,
    engine: Engine,
    tenant_id: UUID,
    email: str,
    display_name: str,
    password: str,
    role: str,
    active: bool = True,
) -> OperatorUser:
    normalized_email = normalize_email(email)
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("display_name is required")
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    if role not in READ_ROLES:
        raise ValueError("Invalid role")

    with engine.begin() as connection:
        user_row = connection.execute(
            text(
                """
                SELECT id
                FROM users
                WHERE email = :email
                FOR UPDATE
                """
            ),
            {"email": normalized_email},
        ).mappings().one_or_none()
        if user_row is None:
            user_id = connection.execute(
                text(
                    """
                    INSERT INTO users (email, display_name, password_hash)
                    VALUES (:email, :display_name, :password_hash)
                    RETURNING id
                    """
                ),
                {
                    "email": normalized_email,
                    "display_name": display_name,
                    "password_hash": hash_password(password),
                },
            ).scalar_one()
        else:
            user_id = user_row["id"]
            connection.execute(
                text(
                    """
                    UPDATE users
                    SET display_name = :display_name,
                        status = 'active',
                        updated_at = now()
                    WHERE id = :user_id
                    """
                ),
                {"display_name": display_name, "user_id": user_id},
            )

        connection.execute(
            text(
                """
                INSERT INTO tenant_memberships (user_id, tenant_id, role, active)
                VALUES (:user_id, :tenant_id, :role, :active)
                ON CONFLICT (user_id, tenant_id) DO UPDATE
                SET role = EXCLUDED.role,
                    active = EXCLUDED.active,
                    updated_at = now()
                """
            ),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "role": role,
                "active": active,
            },
        )

    users = list_tenant_users(engine=engine, tenant_id=tenant_id)
    for user in users:
        if user.user_id == user_id:
            return user
    raise LookupError(f"User not found after creation: {normalized_email}")


def session_has_csrf(*, engine: Engine, session_id: UUID, csrf_token: str) -> bool:
    with engine.connect() as connection:
        stored_hash = connection.execute(
            text(
                """
                SELECT csrf_token_hash
                FROM auth_sessions
                WHERE id = :session_id
                  AND revoked_at IS NULL
                  AND expires_at > now()
                """
            ),
            {"session_id": session_id},
        ).scalar_one_or_none()
    return stored_hash is not None and secrets.compare_digest(stored_hash, hash_token(csrf_token))


def set_auth_cookies(
    *,
    response: Response,
    settings: Settings,
    token: str,
    csrf_token: str,
    expires_at: datetime,
) -> None:
    max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    same_site = settings.auth_cookie_samesite.lower()
    response.set_cookie(
        settings.auth_session_cookie_name,
        token,
        max_age=max_age,
        expires=expires_at,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=same_site,
        path="/",
    )
    response.set_cookie(
        settings.auth_csrf_cookie_name,
        csrf_token,
        max_age=max_age,
        expires=expires_at,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite=same_site,
        path="/",
    )


def clear_auth_cookies(*, response: Response, settings: Settings) -> None:
    for cookie_name in (settings.auth_session_cookie_name, settings.auth_csrf_cookie_name):
        response.delete_cookie(
            cookie_name,
            httponly=cookie_name == settings.auth_session_cookie_name,
            secure=settings.auth_cookie_secure,
            samesite=settings.auth_cookie_samesite.lower(),
            path="/",
        )


def require_auth(
    request: Request,
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    context = load_session_context(
        engine=engine,
        token=request.cookies.get(settings.auth_session_cookie_name),
    )
    if context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    request.state.auth = context
    return context


def require_csrf(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    _verify_origin(request, settings)
    csrf_header = request.headers.get("x-csrf-token")
    csrf_cookie = request.cookies.get(settings.auth_csrf_cookie_name)
    if not csrf_header or not csrf_cookie or not secrets.compare_digest(csrf_header, csrf_cookie):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    if not session_has_csrf(engine=engine, session_id=auth.session_id, csrf_token=csrf_header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    return auth


def require_roles(*roles: str, csrf: bool = False) -> Callable[..., AuthContext]:
    allowed_roles = frozenset(roles)
    base_dependency = require_csrf if csrf else require_auth

    def dependency(auth: AuthContext = Depends(base_dependency)) -> AuthContext:
        if auth.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return auth

    return dependency


def _verify_origin(request: Request, settings: Settings) -> None:
    allowed = {
        value.strip().rstrip("/")
        for value in settings.auth_allowed_origins.split(",")
        if value.strip()
    }
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") in allowed:
        return
    referer = request.headers.get("referer")
    if referer:
        parsed = urlsplit(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if referer_origin in allowed:
            return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid request origin")
