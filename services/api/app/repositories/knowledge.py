import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True)
class KnowledgeItem:
    item_id: UUID
    external_key: str
    title: str
    content: str
    status: str
    risk_class: str
    version: int
    source_path: str | None
    checksum: str | None
    allowed_claims: list[str]
    forbidden_claims: list[str]
    approved_by: str | None
    approved_at: Any | None
    published_at: Any | None
    created_at: Any
    updated_at: Any


@dataclass(frozen=True)
class KnowledgeSearchHit:
    item_id: UUID
    external_key: str
    title: str
    content: str
    risk_class: str
    version: int
    allowed_claims: list[str]
    forbidden_claims: list[str]
    score: float


@dataclass(frozen=True)
class ImportSummary:
    created: int
    updated: int
    unchanged: int
    files: int


def import_knowledge_directory(
    *, engine: Engine, tenant_slug: str, directory: Path
) -> ImportSummary:
    if not directory.exists() or not directory.is_dir():
        raise FileNotFoundError(f"Knowledge directory not found: {directory}")

    files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    created = updated = unchanged = 0

    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        for path in files:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Knowledge file must contain a mapping: {path.name}")

            normalized = _normalize_payload(payload, path)
            result = _upsert_draft(
                connection=connection,
                tenant_id=tenant_id,
                source_path=str(path),
                **normalized,
            )
            if result == "created":
                created += 1
            elif result == "updated":
                updated += 1
            else:
                unchanged += 1

    return ImportSummary(
        created=created,
        updated=updated,
        unchanged=unchanged,
        files=len(files),
    )


def list_knowledge_items(
    *, engine: Engine, tenant_slug: str, status_filter: str | None = None
) -> list[KnowledgeItem]:
    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        rows = connection.execute(
            text(
                """
                SELECT id, external_key, title, content, status, risk_class, version,
                       source_path, checksum, allowed_claims, forbidden_claims,
                       approved_by, approved_at, published_at, created_at, updated_at
                FROM knowledge_items
                WHERE tenant_id = :tenant_id
                  AND (:status_filter IS NULL OR status = :status_filter)
                ORDER BY external_key, version DESC
                """
            ),
            {"tenant_id": tenant_id, "status_filter": status_filter},
        ).mappings().all()
        return [_row_to_item(row) for row in rows]


def publish_knowledge_item(
    *,
    engine: Engine,
    tenant_slug: str,
    external_key: str,
    approved_by: str,
    version: int | None = None,
) -> KnowledgeItem:
    external_key = external_key.strip()
    approved_by = approved_by.strip()
    if not external_key:
        raise ValueError("external_key is required")
    if not approved_by:
        raise ValueError("approved_by is required")

    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        if version is None:
            selection_sql = text(
                """
                SELECT id, version, status
                FROM knowledge_items
                WHERE tenant_id = :tenant_id
                  AND external_key = :external_key
                ORDER BY version DESC
                LIMIT 1
                FOR UPDATE
                """
            )
            selection_params = {
                "tenant_id": tenant_id,
                "external_key": external_key,
            }
        else:
            selection_sql = text(
                """
                SELECT id, version, status
                FROM knowledge_items
                WHERE tenant_id = :tenant_id
                  AND external_key = :external_key
                  AND version = :version
                LIMIT 1
                FOR UPDATE
                """
            )
            selection_params = {
                "tenant_id": tenant_id,
                "external_key": external_key,
                "version": version,
            }

        row = connection.execute(
            selection_sql,
            selection_params,
        ).mappings().one_or_none()
        if row is None:
            raise LookupError(f"Knowledge item not found: {external_key}")

        connection.execute(
            text(
                """
                UPDATE knowledge_items
                SET status = 'archived',
                    updated_at = now()
                WHERE tenant_id = :tenant_id
                  AND external_key = :external_key
                  AND status = 'published'
                  AND id <> :item_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "external_key": external_key,
                "item_id": row["id"],
            },
        )

        connection.execute(
            text(
                """
                UPDATE knowledge_items
                SET status = 'published',
                    approved_by = :approved_by,
                    approved_at = now(),
                    published_at = COALESCE(published_at, now()),
                    updated_at = now()
                WHERE id = :item_id
                """
            ),
            {"item_id": row["id"], "approved_by": approved_by},
        )

        connection.execute(
            text(
                """
                INSERT INTO audit_events (tenant_id, event_type, decision, payload)
                VALUES (
                    :tenant_id,
                    'KNOWLEDGE_PUBLISHED',
                    'PUBLISHED',
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "payload": json.dumps(
                    {
                        "item_id": str(row["id"]),
                        "external_key": external_key,
                        "version": row["version"],
                        "approved_by": approved_by,
                    },
                    ensure_ascii=False,
                ),
            },
        )

        published = connection.execute(
            text(
                """
                SELECT id, external_key, title, content, status, risk_class, version,
                       source_path, checksum, allowed_claims, forbidden_claims,
                       approved_by, approved_at, published_at, created_at, updated_at
                FROM knowledge_items
                WHERE id = :item_id
                """
            ),
            {"item_id": row["id"]},
        ).mappings().one()
        return _row_to_item(published)


def search_published_knowledge(
    *, engine: Engine, tenant_slug: str, query: str, limit: int = 5
) -> list[KnowledgeSearchHit]:
    query = query.strip()
    if not query:
        raise ValueError("query is required")
    limit = min(max(limit, 1), 10)

    with engine.begin() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        rows = connection.execute(
            text(
                """
                WITH search_query AS (
                    SELECT
                        websearch_to_tsquery('spanish', :query) AS tsq,
                        unaccent(lower(:query)) AS normalized_query
                )
                SELECT
                    k.id,
                    k.external_key,
                    k.title,
                    k.content,
                    k.risk_class,
                    k.version,
                    k.allowed_claims,
                    k.forbidden_claims,
                    (
                        ts_rank_cd(k.search_vector, q.tsq) * 2.0
                        + similarity(unaccent(lower(k.title)), q.normalized_query) * 0.8
                        + similarity(unaccent(lower(k.content)), q.normalized_query) * 0.2
                    ) AS score
                FROM knowledge_items k
                CROSS JOIN search_query q
                WHERE k.tenant_id = :tenant_id
                  AND k.status = 'published'
                  AND (
                      k.search_vector @@ q.tsq
                      OR similarity(unaccent(lower(k.title)), q.normalized_query) > 0.12
                      OR similarity(unaccent(lower(k.content)), q.normalized_query) > 0.08
                      OR unaccent(lower(k.title || ' ' || k.content))
                         LIKE '%' || q.normalized_query || '%'
                  )
                ORDER BY score DESC, k.external_key, k.version DESC
                LIMIT :limit
                """
            ),
            {"query": query, "tenant_id": tenant_id, "limit": limit},
        ).mappings().all()

        hits = [
            KnowledgeSearchHit(
                item_id=row["id"],
                external_key=row["external_key"],
                title=row["title"],
                content=row["content"],
                risk_class=row["risk_class"],
                version=row["version"],
                allowed_claims=list(row["allowed_claims"] or []),
                forbidden_claims=list(row["forbidden_claims"] or []),
                score=float(row["score"] or 0.0),
            )
            for row in rows
        ]

        connection.execute(
            text(
                """
                INSERT INTO knowledge_search_events (
                    tenant_id, query_text, result_ids, top_score
                )
                VALUES (
                    :tenant_id,
                    :query_text,
                    CAST(:result_ids AS jsonb),
                    :top_score
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "query_text": query,
                "result_ids": json.dumps([str(hit.item_id) for hit in hits]),
                "top_score": hits[0].score if hits else None,
            },
        )

        return hits



def get_published_knowledge_by_keys(
    *, engine: Engine, tenant_slug: str, external_keys: list[str]
) -> list[KnowledgeItem]:
    keys = [value.strip() for value in external_keys if value and value.strip()]
    if not keys:
        return []

    with engine.connect() as connection:
        tenant_id = _get_active_tenant_id(connection, tenant_slug)
        rows = connection.execute(
            text(
                """
                SELECT id, external_key, title, content, status, risk_class, version,
                       source_path, checksum, allowed_claims, forbidden_claims,
                       approved_by, approved_at, published_at, created_at, updated_at
                FROM knowledge_items
                WHERE tenant_id = :tenant_id
                  AND status = 'published'
                  AND external_key = ANY(CAST(:external_keys AS text[]))
                ORDER BY array_position(CAST(:external_keys AS text[]), external_key)
                """
            ),
            {"tenant_id": tenant_id, "external_keys": keys},
        ).mappings().all()
        return [_row_to_item(row) for row in rows]

def _normalize_payload(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    external_key = str(payload.get("external_key") or "").strip()
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    risk_class = str(payload.get("risk_class") or "low").strip().lower()
    allowed_claims = payload.get("allowed_claims") or []
    forbidden_claims = payload.get("forbidden_claims") or []

    if not external_key or not title or not content:
        raise ValueError(
            f"Knowledge file {path.name} requires external_key, title and content"
        )
    if risk_class not in {"low", "medium", "high"}:
        raise ValueError(f"Invalid risk_class in {path.name}: {risk_class}")
    if not isinstance(allowed_claims, list) or not all(
        isinstance(value, str) for value in allowed_claims
    ):
        raise ValueError(f"allowed_claims must be a string list in {path.name}")
    if not isinstance(forbidden_claims, list) or not all(
        isinstance(value, str) for value in forbidden_claims
    ):
        raise ValueError(f"forbidden_claims must be a string list in {path.name}")

    canonical = {
        "external_key": external_key,
        "title": title,
        "content": content,
        "risk_class": risk_class,
        "allowed_claims": allowed_claims,
        "forbidden_claims": forbidden_claims,
        "source_owner": payload.get("source_owner"),
    }
    checksum = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return {
        **canonical,
        "checksum": checksum,
        "metadata": {
            "source_owner": payload.get("source_owner"),
            "source_file": path.name,
            "imported_status": payload.get("status"),
            "approval": payload.get("approval") or {},
        },
    }


def _upsert_draft(
    *,
    connection: Connection,
    tenant_id: UUID,
    source_path: str,
    external_key: str,
    title: str,
    content: str,
    risk_class: str,
    allowed_claims: list[str],
    forbidden_claims: list[str],
    checksum: str,
    metadata: dict[str, Any],
    source_owner: Any = None,
) -> str:
    latest = connection.execute(
        text(
            """
            SELECT id, version, status, checksum
            FROM knowledge_items
            WHERE tenant_id = :tenant_id
              AND external_key = :external_key
            ORDER BY version DESC
            LIMIT 1
            FOR UPDATE
            """
        ),
        {"tenant_id": tenant_id, "external_key": external_key},
    ).mappings().one_or_none()

    if latest is not None and latest["checksum"] == checksum:
        return "unchanged"

    params = {
        "tenant_id": tenant_id,
        "external_key": external_key,
        "title": title,
        "content": content,
        "risk_class": risk_class,
        "source_path": source_path,
        "checksum": checksum,
        "allowed_claims": json.dumps(allowed_claims, ensure_ascii=False),
        "forbidden_claims": json.dumps(forbidden_claims, ensure_ascii=False),
        "metadata": json.dumps(metadata, ensure_ascii=False),
    }

    if latest is not None and latest["status"] == "draft":
        connection.execute(
            text(
                """
                UPDATE knowledge_items
                SET title = :title,
                    content = :content,
                    risk_class = :risk_class,
                    source_path = :source_path,
                    checksum = :checksum,
                    allowed_claims = CAST(:allowed_claims AS jsonb),
                    forbidden_claims = CAST(:forbidden_claims AS jsonb),
                    metadata = CAST(:metadata AS jsonb),
                    updated_at = now()
                WHERE id = :item_id
                """
            ),
            {**params, "item_id": latest["id"]},
        )
        return "updated"

    next_version = 1 if latest is None else int(latest["version"]) + 1
    connection.execute(
        text(
            """
            INSERT INTO knowledge_items (
                tenant_id, external_key, title, content, status, risk_class,
                version, metadata, source_path, checksum, allowed_claims,
                forbidden_claims, updated_at
            )
            VALUES (
                :tenant_id, :external_key, :title, :content, 'draft', :risk_class,
                :version, CAST(:metadata AS jsonb), :source_path, :checksum,
                CAST(:allowed_claims AS jsonb), CAST(:forbidden_claims AS jsonb), now()
            )
            """
        ),
        {**params, "version": next_version},
    )
    return "created"


def _row_to_item(row: Any) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=row["id"],
        external_key=row["external_key"],
        title=row["title"],
        content=row["content"],
        status=row["status"],
        risk_class=row["risk_class"],
        version=row["version"],
        source_path=row["source_path"],
        checksum=row["checksum"],
        allowed_claims=list(row["allowed_claims"] or []),
        forbidden_claims=list(row["forbidden_claims"] or []),
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        published_at=row["published_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _get_active_tenant_id(connection: Connection, tenant_slug: str) -> UUID:
    tenant_id = connection.execute(
        text("SELECT id FROM tenants WHERE slug = :slug AND status = 'active'"),
        {"slug": tenant_slug},
    ).scalar_one_or_none()
    if tenant_id is None:
        raise LookupError(f"Active tenant not found: {tenant_slug}")
    return tenant_id
