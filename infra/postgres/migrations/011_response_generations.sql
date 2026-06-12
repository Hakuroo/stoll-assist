CREATE TABLE IF NOT EXISTS response_generations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    message_id uuid NOT NULL REFERENCES messages(id),
    response_plan_id uuid NOT NULL REFERENCES response_plans(id),
    provider text NOT NULL,
    model text NOT NULL,
    prompt_version text NOT NULL,
    status text NOT NULL,
    provider_request_id text,
    input_hash text NOT NULL,
    structured_output jsonb,
    latency_ms integer,
    token_usage jsonb,
    error_code text,
    error_message text,
    attempt_count integer NOT NULL DEFAULT 0,
    lease_owner text,
    lease_expires_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, response_plan_id),
    CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'HANDOFF', 'FAILED')),
    CHECK (attempt_count >= 0),
    CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

ALTER TABLE response_generations
    ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS lease_owner text,
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS started_at timestamptz,
    ADD COLUMN IF NOT EXISTS completed_at timestamptz,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_response_generations_message_created
    ON response_generations (tenant_id, message_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_response_generations_status_created
    ON response_generations (tenant_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_response_generations_active_lease
    ON response_generations (tenant_id, lease_expires_at)
    WHERE status = 'IN_PROGRESS';
