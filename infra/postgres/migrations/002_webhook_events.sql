CREATE TABLE IF NOT EXISTS webhook_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    provider text NOT NULL,
    provider_event_id text NOT NULL,
    event_kind text NOT NULL DEFAULT 'unknown',
    signature_valid boolean NOT NULL DEFAULT true,
    status text NOT NULL DEFAULT 'RECEIVED',
    payload jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    error_message text,
    UNIQUE (tenant_id, provider, provider_event_id),
    CHECK (status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'IGNORED', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_tenant_status_received
    ON webhook_events (tenant_id, status, received_at);
