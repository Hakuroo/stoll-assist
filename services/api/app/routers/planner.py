from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.auth import OPERATE_ROLES, AuthContext, require_roles
from app.database import get_engine
from app.schemas import ResponsePlanPreviewRequest, ResponsePlanResponse
from app.services.response_planner import preview_response_plan
router = APIRouter(prefix="/operator/planner", tags=["response-planner"])


@router.post("/preview", response_model=ResponsePlanResponse)
def preview_plan(
    request: ResponsePlanPreviewRequest,
    auth: AuthContext = Depends(require_roles(*OPERATE_ROLES, csrf=True)),
) -> ResponsePlanResponse:
    try:
        plan = preview_response_plan(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            text=request.text,
            conversation_state=request.conversation_state,
        )
        return ResponsePlanResponse.from_plan(plan)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (LookupError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Response plan could not be created") from exc
