ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS outbound_mode text NOT NULL DEFAULT 'REVIEW_REQUIRED';

ALTER TABLE tenants
    DROP CONSTRAINT IF EXISTS tenants_outbound_mode_check;

ALTER TABLE tenants
    ADD CONSTRAINT tenants_outbound_mode_check
    CHECK (outbound_mode IN ('DISABLED', 'REVIEW_REQUIRED', 'AUTO_LOW_RISK'));

CREATE TABLE IF NOT EXISTS outbound_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    in_reply_to_message_id uuid NOT NULL REFERENCES messages(id),
    plan_id uuid NOT NULL REFERENCES response_plans(id),
    verification_id uuid NOT NULL REFERENCES response_verifications(id),
    channel text NOT NULL DEFAULT 'whatsapp',
    recipient text NOT NULL,
    body_text text NOT NULL,
    body_sha256 text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING_REVIEW',
    requires_review boolean NOT NULL DEFAULT true,
    approved_by text,
    approved_at timestamptz,
    rejected_by text,
    rejected_at timestamptz,
    rejection_reason text,
    provider_message_id text,
    send_attempt_count integer NOT NULL DEFAULT 0,
    last_attempt_at timestamptz,
    sent_at timestamptz,
    failed_at timestamptz,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, verification_id),
    CHECK (channel IN ('whatsapp')),
    CHECK (
        status IN (
            'PENDING_REVIEW',
            'APPROVED',
            'REJECTED',
            'QUEUED',
            'SENT',
            'FAILED',
            'CANCELLED'
        )
    ),
    CHECK (char_length(body_text) BETWEEN 1 AND 4096),
    CHECK (send_attempt_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_outbound_messages_status_created
    ON outbound_messages (tenant_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_outbound_messages_conversation_created
    ON outbound_messages (tenant_id, conversation_id, created_at DESC);
