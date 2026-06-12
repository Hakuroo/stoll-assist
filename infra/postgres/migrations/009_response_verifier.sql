CREATE TABLE IF NOT EXISTS response_verifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    plan_id uuid NOT NULL REFERENCES response_plans(id),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    message_id uuid NOT NULL REFERENCES messages(id),
    status text NOT NULL,
    reason_code text NOT NULL,
    checks jsonb NOT NULL DEFAULT '{}'::jsonb,
    unsupported_claims jsonb NOT NULL DEFAULT '[]'::jsonb,
    verifier_version text NOT NULL DEFAULT '0.9.0',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, plan_id),
    CHECK (status IN ('APPROVED', 'REJECTED', 'SKIPPED'))
);

CREATE INDEX IF NOT EXISTS idx_response_verifications_conversation_created
    ON response_verifications (tenant_id, conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_response_verifications_status_created
    ON response_verifications (tenant_id, status, created_at DESC);
