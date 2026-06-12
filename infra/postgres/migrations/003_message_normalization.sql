ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS provider_timestamp timestamptz;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_contacts_tenant_whatsapp_user
    ON contacts (tenant_id, whatsapp_user_id);

CREATE INDEX IF NOT EXISTS idx_conversations_tenant_contact_state
    ON conversations (tenant_id, contact_id, state, last_message_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages (conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_tenant_provider_message
    ON messages (tenant_id, provider_message_id);
