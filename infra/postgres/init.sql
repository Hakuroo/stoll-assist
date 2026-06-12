CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text UNIQUE NOT NULL,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    agent_name text NOT NULL DEFAULT 'Agustina',
    agent_disclosure text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    whatsapp_user_id text NOT NULL,
    display_name text,
    phone_e164 text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, whatsapp_user_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    contact_id uuid NOT NULL REFERENCES contacts(id),
    state text NOT NULL DEFAULT 'AUTOMATED',
    assigned_user_id uuid,
    last_message_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state IN ('AUTOMATED', 'HUMAN_REQUIRED', 'HUMAN_ACTIVE', 'CLOSED'))
);

CREATE TABLE IF NOT EXISTS messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    provider_message_id text NOT NULL,
    direction text NOT NULL,
    message_type text NOT NULL,
    body_text text,
    provider_timestamp timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider_message_id),
    CHECK (direction IN ('INBOUND', 'OUTBOUND'))
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    external_key text NOT NULL,
    title text NOT NULL,
    content text NOT NULL,
    status text NOT NULL DEFAULT 'draft',
    risk_class text NOT NULL DEFAULT 'low',
    version integer NOT NULL DEFAULT 1,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536),
    approved_by text,
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, external_key, version),
    CHECK (status IN ('draft', 'published', 'archived'))
);

CREATE TABLE IF NOT EXISTS policy_rules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    rule_key text NOT NULL,
    rule_type text NOT NULL,
    description text NOT NULL,
    action text NOT NULL,
    priority integer NOT NULL DEFAULT 100,
    enabled boolean NOT NULL DEFAULT true,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tenant_id, rule_key)
);

CREATE TABLE IF NOT EXISTS handoffs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    reason_code text NOT NULL,
    summary text,
    status text NOT NULL DEFAULT 'OPEN',
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    CHECK (status IN ('OPEN', 'TAKEN', 'RESOLVED'))
);

CREATE TABLE IF NOT EXISTS audit_events (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    conversation_id uuid,
    event_type text NOT NULL,
    decision text,
    model_name text,
    prompt_version text,
    knowledge_ids uuid[],
    latency_ms integer,
    token_usage jsonb,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO tenants (slug, name, agent_name, agent_disclosure)
VALUES (
    'grupo-stoll',
    'Grupo Stöll',
    'Agustina',
    'Soy Agustina, asistente digital del equipo de Grupo Stöll.'
)
ON CONFLICT (slug) DO NOTHING;

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
    queued_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0,
    last_attempt_at timestamptz,
    processed_at timestamptz,
    error_message text,
    UNIQUE (tenant_id, provider, provider_event_id),
    CHECK (status IN ('RECEIVED', 'QUEUED', 'PROCESSING', 'PROCESSED', 'IGNORED', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_tenant_status_received
    ON webhook_events (tenant_id, status, received_at);

CREATE INDEX IF NOT EXISTS idx_webhook_events_queue_status
    ON webhook_events (status, queued_at)
    WHERE status IN ('QUEUED', 'PROCESSING', 'FAILED');


CREATE INDEX IF NOT EXISTS idx_contacts_tenant_whatsapp_user
    ON contacts (tenant_id, whatsapp_user_id);

CREATE INDEX IF NOT EXISTS idx_conversations_tenant_contact_state
    ON conversations (tenant_id, contact_id, state, last_message_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages (conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_tenant_provider_message
    ON messages (tenant_id, provider_message_id);
