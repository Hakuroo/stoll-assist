CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

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
    assigned_operator text,
    last_state_reason text,
    state_changed_at timestamptz NOT NULL DEFAULT now(),
    automation_suspended_at timestamptz,
    closed_at timestamptz,
    state_version integer NOT NULL DEFAULT 0,
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
    source_path text,
    checksum text,
    allowed_claims jsonb NOT NULL DEFAULT '[]'::jsonb,
    forbidden_claims jsonb NOT NULL DEFAULT '[]'::jsonb,
    approved_by text,
    approved_at timestamptz,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('spanish', coalesce(title, '') || ' ' || coalesce(content, ''))
    ) STORED,
    UNIQUE (tenant_id, external_key, version),
    CHECK (status IN ('draft', 'published', 'archived'))
);

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
    requested_by text,
    taken_by text,
    taken_at timestamptz,
    resolved_by text,
    resolution_note text,
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


CREATE INDEX IF NOT EXISTS idx_conversations_tenant_state_changed
    ON conversations (tenant_id, state, state_changed_at DESC);

CREATE INDEX IF NOT EXISTS idx_handoffs_conversation_active
    ON handoffs (conversation_id, created_at DESC)
    WHERE status IN ('OPEN', 'TAKEN');



CREATE TABLE IF NOT EXISTS policy_evaluations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    message_id uuid NOT NULL REFERENCES messages(id),
    decision text NOT NULL,
    matched_rule_key text,
    risk_level text NOT NULL DEFAULT 'medium',
    reason text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    evaluated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, message_id),
    CHECK (decision IN ('ALLOW', 'HANDOFF', 'IGNORE')),
    CHECK (risk_level IN ('low', 'medium', 'high'))
);

CREATE INDEX IF NOT EXISTS idx_policy_evaluations_conversation
    ON policy_evaluations (tenant_id, conversation_id, evaluated_at DESC);

WITH target_tenant AS (
    SELECT id FROM tenants WHERE slug = 'grupo-stoll'
), rules(rule_key, description, action, priority, config) AS (
    VALUES
    (
        'customer_requests_human',
        'El cliente pidió hablar con una persona.',
        'HANDOFF',
        10,
        jsonb_build_object(
            'risk_level', 'high',
            'terms', jsonb_build_array(
                'quiero hablar con una persona',
                'quiero hablar con alguien',
                'pasame con una persona',
                'que me atienda alguien',
                'asesor humano',
                'hablar con un asesor'
            )
        )
    ),
    (
        'complaint',
        'Los reclamos deben ser tratados por una persona.',
        'HANDOFF',
        20,
        jsonb_build_object(
            'risk_level', 'high',
            'terms', jsonb_build_array(
                'quiero hacer un reclamo', 'reclamo', 'denuncia', 'estafa',
                'no cumplieron', 'incumplimiento', 'problema con la obra'
            )
        )
    ),
    (
        'legal_question',
        'Las consultas legales o contractuales requieren revisión humana.',
        'HANDOFF',
        30,
        jsonb_build_object(
            'risk_level', 'high',
            'patterns', jsonb_build_array(
                '\b(abogado|carta documento|demanda|legal|clausula|responsabilidad civil)\b'
            )
        )
    ),
    (
        'structural_calculation',
        'Los cálculos estructurales requieren revisión técnica profesional.',
        'HANDOFF',
        40,
        jsonb_build_object(
            'risk_level', 'high',
            'terms', jsonb_build_array(
                'calculo estructural', 'dimensionar una viga', 'dimensionar la estructura'
            ),
            'patterns', jsonb_build_array(
                '\b(calcular|dimensionar)\b.{0,50}\b(viga|columna|estructura|perfil|fundacion)\b'
            )
        )
    ),
    (
        'profile_selection',
        'La selección de perfiles o componentes estructurales requiere revisión técnica.',
        'HANDOFF',
        45,
        jsonb_build_object(
            'risk_level', 'high',
            'terms', jsonb_build_array(
                'que perfil usar', 'cual perfil usar', 'que hierro usar', 'que viga usar'
            )
        )
    ),
    (
        'safety_or_load_claim',
        'Las afirmaciones de seguridad, carga o resistencia requieren validación técnica.',
        'HANDOFF',
        50,
        jsonb_build_object(
            'risk_level', 'high',
            'patterns', jsonb_build_array(
                '\b(aguanta|soporta|resiste|es seguro)\b.{0,60}\b(peso|viento|carga|estructura|techo)\b'
            )
        )
    ),
    (
        'exact_price',
        'Los precios y estimaciones comerciales deben ser revisados por una persona.',
        'HANDOFF',
        60,
        jsonb_build_object(
            'risk_level', 'medium',
            'terms', jsonb_build_array(
                'cuanto sale', 'cuanto cuesta', 'precio exacto', 'precio por metro',
                'valor por metro', 'necesito un presupuesto', 'pasame un presupuesto'
            ),
            'patterns', jsonb_build_array(
                '\b(precio|costo|valor|presupuesto)\b'
            )
        )
    ),
    (
        'discount_or_payment_negotiation',
        'Descuentos, financiación y condiciones de pago requieren intervención comercial.',
        'HANDOFF',
        70,
        jsonb_build_object(
            'risk_level', 'medium',
            'terms', jsonb_build_array(
                'descuento', 'forma de pago', 'pago en cuotas', 'financiacion',
                'cuanto anticipo', 'condiciones de pago'
            )
        )
    ),
    (
        'guaranteed_deadline',
        'No se pueden garantizar fechas o plazos sin revisión humana.',
        'HANDOFF',
        80,
        jsonb_build_object(
            'risk_level', 'high',
            'terms', jsonb_build_array(
                'me garantizas', 'me aseguras', 'llegan para', 'terminan para',
                'fecha garantizada', 'plazo garantizado'
            )
        )
    ),
    (
        'supplier_or_internal_information',
        'La información interna, de costos o proveedores no debe divulgarse automáticamente.',
        'HANDOFF',
        90,
        jsonb_build_object(
            'risk_level', 'high',
            'terms', jsonb_build_array(
                'costos internos', 'margen de ganancia', 'quien es su proveedor',
                'a quien le compran', 'donde compran los materiales'
            )
        )
    ),
    (
        'competitor_comparison',
        'Las comparaciones comerciales con competidores requieren intervención humana.',
        'HANDOFF',
        100,
        jsonb_build_object(
            'risk_level', 'medium',
            'terms', jsonb_build_array(
                'son mas baratos que', 'comparado con', 'me conviene ustedes',
                'que diferencia tienen con'
            )
        )
    )
)
INSERT INTO policy_rules (
    tenant_id,
    rule_key,
    rule_type,
    description,
    action,
    priority,
    enabled,
    config
)
SELECT
    target_tenant.id,
    rules.rule_key,
    'text_match',
    rules.description,
    rules.action,
    rules.priority,
    true,
    rules.config
FROM target_tenant
CROSS JOIN rules
ON CONFLICT (tenant_id, rule_key)
DO UPDATE SET
    description = EXCLUDED.description,
    action = EXCLUDED.action,
    priority = EXCLUDED.priority,
    enabled = EXCLUDED.enabled,
    config = EXCLUDED.config;

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
