from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_engine
from app.repositories.knowledge import (
    import_knowledge_directory,
    list_knowledge_items,
    publish_knowledge_item,
    search_published_knowledge,
)
from app.schemas import (
    KnowledgeImportResponse,
    KnowledgeItemResponse,
    KnowledgePublishRequest,
    KnowledgeSearchHitResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.settings import get_settings

router = APIRouter(prefix="/operator/knowledge", tags=["operator-knowledge"])
settings = get_settings()


@router.post("/import-config", response_model=KnowledgeImportResponse)
def import_config_knowledge() -> KnowledgeImportResponse:
    try:
        summary = import_knowledge_directory(
            engine=get_engine(),
            tenant_slug=settings.default_tenant_slug,
            directory=Path(settings.knowledge_config_path),
        )
        return KnowledgeImportResponse(
            files=summary.files,
            created=summary.created,
            updated=summary.updated,
            unchanged=summary.unchanged,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (LookupError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Knowledge could not be imported") from exc


@router.get("", response_model=list[KnowledgeItemResponse])
def get_knowledge_items(
    status_filter: str | None = Query(default=None, alias="status")
) -> list[KnowledgeItemResponse]:
    if status_filter is not None and status_filter not in {"draft", "published", "archived"}:
        raise HTTPException(status_code=400, detail="Invalid knowledge status")
    try:
        items = list_knowledge_items(
            engine=get_engine(),
            tenant_slug=settings.default_tenant_slug,
            status_filter=status_filter,
        )
        return [KnowledgeItemResponse.from_item(item) for item in items]
    except (LookupError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Knowledge could not be read") from exc


@router.post("/{external_key}/publish", response_model=KnowledgeItemResponse)
def publish_item(
    external_key: str,
    request: KnowledgePublishRequest,
) -> KnowledgeItemResponse:
    try:
        item = publish_knowledge_item(
            engine=get_engine(),
            tenant_slug=settings.default_tenant_slug,
            external_key=external_key,
            approved_by=request.approved_by,
            version=request.version,
        )
        return KnowledgeItemResponse.from_item(item)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Knowledge could not be published") from exc


@router.post("/search", response_model=KnowledgeSearchResponse)
def search_knowledge(request: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
    try:
        hits = search_published_knowledge(
            engine=get_engine(),
            tenant_slug=settings.default_tenant_slug,
            query=request.query,
            limit=request.limit,
        )
        return KnowledgeSearchResponse(
            query=request.query,
            hits=[
                KnowledgeSearchHitResponse(
                    item_id=hit.item_id,
                    external_key=hit.external_key,
                    title=hit.title,
                    excerpt=hit.content[:500],
                    risk_class=hit.risk_class,
                    version=hit.version,
                    allowed_claims=hit.allowed_claims,
                    forbidden_claims=hit.forbidden_claims,
                    score=round(hit.score, 6),
                )
                for hit in hits
            ],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (LookupError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Knowledge search failed") from exc
