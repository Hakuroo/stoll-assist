CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

ALTER TABLE knowledge_items
    ADD COLUMN IF NOT EXISTS source_path text,
    ADD COLUMN IF NOT EXISTS checksum text,
    ADD COLUMN IF NOT EXISTS allowed_claims jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS forbidden_claims jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS published_at timestamptz,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('spanish', coalesce(title, '') || ' ' || coalesce(content, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_knowledge_items_tenant_status
    ON knowledge_items (tenant_id, status, external_key, version DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_items_search_vector
    ON knowledge_items USING gin (search_vector);

CREATE INDEX IF NOT EXISTS idx_knowledge_items_title_trgm
    ON knowledge_items USING gin (title gin_trgm_ops);

CREATE TABLE IF NOT EXISTS knowledge_search_events (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    query_text text NOT NULL,
    result_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    top_score double precision,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_search_events_tenant_created
    ON knowledge_search_events (tenant_id, created_at DESC);
