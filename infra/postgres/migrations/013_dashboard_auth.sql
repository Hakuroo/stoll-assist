CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text UNIQUE NOT NULL,
    display_name text NOT NULL,
    password_hash text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz,
    CHECK (email = lower(trim(email))),
    CHECK (status IN ('active', 'disabled'))
);

CREATE TABLE IF NOT EXISTS tenant_memberships (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, tenant_id),
    CHECK (role IN ('OWNER', 'ADMIN', 'OPERATOR', 'VIEWER'))
);

CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant_role
    ON tenant_memberships (tenant_id, role)
    WHERE active;

CREATE TABLE IF NOT EXISTS auth_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    token_hash text UNIQUE NOT NULL,
    csrf_token_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    user_agent_hash text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_active
    ON auth_sessions (user_id, expires_at DESC)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_auth_sessions_tenant_active
    ON auth_sessions (tenant_id, expires_at DESC)
    WHERE revoked_at IS NULL;
