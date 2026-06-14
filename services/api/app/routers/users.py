from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.auth import (
    ADMIN_ROLES,
    AuthContext,
    create_or_update_tenant_user,
    list_tenant_users,
    require_roles,
)
from app.database import get_engine
from app.schemas import OperatorUserCreateRequest, OperatorUserResponse

router = APIRouter(prefix="/operator/users", tags=["operator-users"])


@router.get("", response_model=list[OperatorUserResponse])
def list_users(
    auth: AuthContext = Depends(require_roles(*ADMIN_ROLES)),
    engine: Engine = Depends(get_engine),
) -> list[OperatorUserResponse]:
    try:
        users = list_tenant_users(engine=engine, tenant_id=auth.tenant_id)
        return [_user_response(user) for user in users]
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Users could not be listed") from exc


@router.post("", response_model=OperatorUserResponse)
def create_user(
    payload: OperatorUserCreateRequest,
    auth: AuthContext = Depends(require_roles(*ADMIN_ROLES, csrf=True)),
    engine: Engine = Depends(get_engine),
) -> OperatorUserResponse:
    try:
        user = create_or_update_tenant_user(
            engine=engine,
            tenant_id=auth.tenant_id,
            email=payload.email,
            display_name=payload.display_name,
            password=payload.password,
            role=payload.role.value,
            active=payload.active,
        )
        return _user_response(user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="User could not be created") from exc


def _user_response(user) -> OperatorUserResponse:
    return OperatorUserResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        role=user.role,
        membership_active=user.membership_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )
