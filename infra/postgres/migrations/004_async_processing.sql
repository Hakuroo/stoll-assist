ALTER TABLE webhook_events
    ADD COLUMN IF NOT EXISTS queued_at timestamptz,
    ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz;

-- The original webhook status constraint did not include QUEUED.
-- Recreate it so asynchronous ingestion can move RECEIVED -> QUEUED.
ALTER TABLE webhook_events
    DROP CONSTRAINT IF EXISTS webhook_events_status_check;

ALTER TABLE webhook_events
    ADD CONSTRAINT webhook_events_status_check
    CHECK (
        status IN (
            'RECEIVED',
            'QUEUED',
            'PROCESSING',
            'PROCESSED',
            'IGNORED',
            'FAILED'
        )
    );

CREATE INDEX IF NOT EXISTS idx_webhook_events_queue_status
    ON webhook_events (status, queued_at)
    WHERE status IN ('QUEUED', 'PROCESSING', 'FAILED');
