from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.auth import OPERATE_ROLES, AuthContext, require_roles
from app.database import get_engine
from app.schemas import ResponseVerificationPreviewRequest, ResponseVerificationResponse
from app.services.response_verifier import preview_verification
router = APIRouter(prefix="/operator/verifier", tags=["response-verifier"])


@router.post("/preview", response_model=ResponseVerificationResponse)
def preview(
    request: ResponseVerificationPreviewRequest,
    auth: AuthContext = Depends(require_roles(*OPERATE_ROLES, csrf=True)),
) -> ResponseVerificationResponse:
    try:
        result = preview_verification(
            engine=get_engine(),
            tenant_slug=auth.tenant_slug,
            decision=request.decision.value,
            draft_reply=request.draft_reply,
            knowledge_keys=request.knowledge_keys,
            forbidden_claims=request.forbidden_claims,
        )
        return ResponseVerificationResponse.from_verification(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (LookupError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Response could not be verified") from exc
