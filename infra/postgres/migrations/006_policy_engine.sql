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
