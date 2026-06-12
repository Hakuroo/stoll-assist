from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_engine
from app.schemas import ResponseVerificationPreviewRequest, ResponseVerificationResponse
from app.services.response_verifier import preview_verification
from app.settings import get_settings

router = APIRouter(prefix="/operator/verifier", tags=["response-verifier"])
settings = get_settings()


@router.post("/preview", response_model=ResponseVerificationResponse)
def preview(request: ResponseVerificationPreviewRequest) -> ResponseVerificationResponse:
    try:
        result = preview_verification(
            engine=get_engine(),
            tenant_slug=settings.default_tenant_slug,
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
