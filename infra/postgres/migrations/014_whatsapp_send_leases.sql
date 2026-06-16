ALTER TABLE outbound_messages
    ADD COLUMN IF NOT EXISTS lease_owner text,
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS current_send_attempt_id uuid,
    ADD COLUMN IF NOT EXISTS unknown_at timestamptz,
    ADD COLUMN IF NOT EXISTS delivery_status text,
    ADD COLUMN IF NOT EXISTS delivered_at timestamptz,
    ADD COLUMN IF NOT EXISTS read_at timestamptz,
    ADD COLUMN IF NOT EXISTS provider_failed_at timestamptz,
    ADD COLUMN IF NOT EXISTS delivery_error_code text,
    ADD COLUMN IF NOT EXISTS delivery_error_message text;

ALTER TABLE outbound_messages
    DROP CONSTRAINT IF EXISTS outbound_messages_status_check;

ALTER TABLE outbound_messages
    ADD CONSTRAINT outbound_messages_status_check
    CHECK (
        status IN (
            'PENDING_REVIEW',
            'APPROVED',
            'REJECTED',
            'QUEUED',
            'SENT',
            'FAILED',
            'UNKNOWN',
            'CANCELLED'
        )
    );

ALTER TABLE outbound_messages
    DROP CONSTRAINT IF EXISTS outbound_messages_delivery_status_check;

ALTER TABLE outbound_messages
    ADD CONSTRAINT outbound_messages_delivery_status_check
    CHECK (
        delivery_status IS NULL
        OR delivery_status IN ('SENT', 'DELIVERED', 'READ', 'FAILED')
    );

CREATE INDEX IF NOT EXISTS idx_outbound_messages_active_send_lease
    ON outbound_messages (tenant_id, lease_expires_at)
    WHERE status = 'QUEUED';

CREATE INDEX IF NOT EXISTS idx_outbound_messages_provider_message
    ON outbound_messages (tenant_id, provider_message_id)
    WHERE provider_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS outbound_send_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    outbound_message_id uuid NOT NULL REFERENCES outbound_messages(id) ON DELETE CASCADE,
    attempt_number integer NOT NULL,
    lease_owner text NOT NULL,
    status text NOT NULL DEFAULT 'CLAIMED',
    provider_message_id text,
    request_started boolean NOT NULL DEFAULT false,
    latency_ms integer,
    error_type text,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, outbound_message_id, attempt_number),
    CHECK (attempt_number > 0),
    CHECK (status IN ('CLAIMED', 'SENT', 'FAILED', 'UNKNOWN')),
    CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_outbound_send_attempts_outbound_created
    ON outbound_send_attempts (tenant_id, outbound_message_id, created_at DESC);

CREATE TABLE IF NOT EXISTS outbound_delivery_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    outbound_message_id uuid NOT NULL REFERENCES outbound_messages(id) ON DELETE CASCADE,
    provider_message_id text NOT NULL,
    delivery_status text NOT NULL,
    provider_timestamp timestamptz,
    error_code text,
    error_message text,
    dedupe_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, dedupe_key),
    CHECK (delivery_status IN ('SENT', 'DELIVERED', 'READ', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_outbound_delivery_events_provider_created
    ON outbound_delivery_events (tenant_id, provider_message_id, created_at DESC);
