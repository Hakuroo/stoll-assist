ALTER TABLE response_generations
    ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS lease_owner text,
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS started_at timestamptz,
    ADD COLUMN IF NOT EXISTS completed_at timestamptz,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE response_generations
    DROP CONSTRAINT IF EXISTS response_generations_status_check;

UPDATE response_generations
SET status = 'COMPLETED'
WHERE status = 'SUCCEEDED';

UPDATE response_generations
SET attempt_count = 1
WHERE attempt_count = 0
  AND status IN ('COMPLETED', 'HANDOFF', 'FAILED');

UPDATE response_generations
SET completed_at = COALESCE(completed_at, created_at),
    updated_at = COALESCE(updated_at, created_at)
WHERE status IN ('COMPLETED', 'HANDOFF', 'FAILED');

ALTER TABLE response_generations
    ADD CONSTRAINT response_generations_status_check
    CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'HANDOFF', 'FAILED'));

ALTER TABLE response_generations
    DROP CONSTRAINT IF EXISTS response_generations_attempt_count_check;

ALTER TABLE response_generations
    ADD CONSTRAINT response_generations_attempt_count_check
    CHECK (attempt_count >= 0);

CREATE INDEX IF NOT EXISTS idx_response_generations_active_lease
    ON response_generations (tenant_id, lease_expires_at)
    WHERE status = 'IN_PROGRESS';
