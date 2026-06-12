ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS assigned_operator text,
    ADD COLUMN IF NOT EXISTS last_state_reason text,
    ADD COLUMN IF NOT EXISTS state_changed_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS automation_suspended_at timestamptz,
    ADD COLUMN IF NOT EXISTS closed_at timestamptz,
    ADD COLUMN IF NOT EXISTS state_version integer NOT NULL DEFAULT 0;

ALTER TABLE handoffs
    ADD COLUMN IF NOT EXISTS requested_by text,
    ADD COLUMN IF NOT EXISTS taken_by text,
    ADD COLUMN IF NOT EXISTS taken_at timestamptz,
    ADD COLUMN IF NOT EXISTS resolved_by text,
    ADD COLUMN IF NOT EXISTS resolution_note text;

CREATE INDEX IF NOT EXISTS idx_conversations_tenant_state_changed
    ON conversations (tenant_id, state, state_changed_at DESC);

CREATE INDEX IF NOT EXISTS idx_handoffs_conversation_active
    ON handoffs (conversation_id, created_at DESC)
    WHERE status IN ('OPEN', 'TAKEN');
