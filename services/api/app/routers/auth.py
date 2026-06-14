from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import Engine

from app.auth import (
    AuthContext,
    authenticate_and_create_session,
    clear_auth_cookies,
    require_auth,
    require_csrf,
    revoke_all_sessions,
    revoke_session,
    set_auth_cookies,
)
from app.database import get_engine
from app.schemas import AuthLoginRequest, AuthUserResponse
from app.settings import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=AuthUserResponse)
def login(
    request: Request,
    response: Response,
    payload: AuthLoginRequest,
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
) -> AuthUserResponse:
    try:
        created = authenticate_and_create_session(
            engine=engine,
            settings=settings,
            email=payload.email,
            password=payload.password,
            tenant_slug=payload.tenant_slug or settings.default_tenant_slug,
            user_agent=request.headers.get("user-agent"),
        )
    except ValueError:
        created = None
    if created is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    set_auth_cookies(
        response=response,
        settings=settings,
        token=created.token,
        csrf_token=created.csrf_token,
        expires_at=created.context.expires_at,
    )
    return _auth_response(created.context)


@router.post("/logout", response_model=dict[str, bool])
def logout(
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    revoke_session(engine=engine, session_id=auth.session_id)
    clear_auth_cookies(response=response, settings=settings)
    return {"ok": True}


@router.post("/logout-all", response_model=dict[str, bool])
def logout_all(
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    revoke_all_sessions(engine=engine, user_id=auth.user_id)
    clear_auth_cookies(response=response, settings=settings)
    return {"ok": True}


@router.get("/me", response_model=AuthUserResponse)
def me(auth: AuthContext = Depends(require_auth)) -> AuthUserResponse:
    return _auth_response(auth)


def _auth_response(auth: AuthContext) -> AuthUserResponse:
    return AuthUserResponse(
        user_id=auth.user_id,
        email=auth.email,
        display_name=auth.display_name,
        tenant_id=auth.tenant_id,
        tenant_slug=auth.tenant_slug,
        tenant_name=auth.tenant_name,
        role=auth.role,
        expires_at=auth.expires_at,
    )
