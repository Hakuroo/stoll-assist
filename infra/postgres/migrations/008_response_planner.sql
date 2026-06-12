CREATE TABLE IF NOT EXISTS response_plans (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    message_id uuid NOT NULL REFERENCES messages(id),
    decision text NOT NULL,
    reason_code text NOT NULL,
    risk_level text NOT NULL DEFAULT 'low',
    policy_rule_key text,
    knowledge_item_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    knowledge_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
    allowed_claims jsonb NOT NULL DEFAULT '[]'::jsonb,
    forbidden_claims jsonb NOT NULL DEFAULT '[]'::jsonb,
    reply_goal text NOT NULL,
    draft_reply text,
    planner_version text NOT NULL DEFAULT '0.8.0',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, message_id),
    CHECK (decision IN ('ANSWER', 'ASK', 'HANDOFF', 'IGNORE')),
    CHECK (risk_level IN ('low', 'medium', 'high'))
);

CREATE INDEX IF NOT EXISTS idx_response_plans_conversation_created
    ON response_plans (tenant_id, conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_response_plans_decision_created
    ON response_plans (tenant_id, decision, created_at DESC);
