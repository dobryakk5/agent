-- Миграция для существующей БД
-- psql "$DATABASE_URL" -f migrate_users.sql

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE user_instances
    ADD COLUMN IF NOT EXISTS google_connected    BOOLEAN     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS google_connected_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS api_key             TEXT        NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS secrets_volume_name TEXT        NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS gateway_token       TEXT        NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS user_platform       TEXT,
    ADD COLUMN IF NOT EXISTS user_llm_model      TEXT,
    ADD COLUMN IF NOT EXISTS user_api_key        TEXT        NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS user_tool_use_model TEXT;

UPDATE user_instances
SET gateway_token = gen_random_uuid()::text
WHERE gateway_token = '';

CREATE TABLE IF NOT EXISTS oauth_states (
    id         UUID PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider   TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS oauth_states_user_provider_expires_idx ON oauth_states (user_id, provider, expires_at);

CREATE TABLE IF NOT EXISTS telegram_link_tokens (
    token      TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS telegram_link_tokens_user_expires_idx ON telegram_link_tokens (user_id, expires_at);

CREATE TABLE IF NOT EXISTS telegram_links (
    user_id              BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    telegram_user_id     BIGINT NOT NULL UNIQUE,
    telegram_chat_id     BIGINT NOT NULL,
    telegram_username    TEXT,
    telegram_first_name  TEXT,
    telegram_last_name   TEXT,
    linked_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS telegram_links_telegram_user_id_idx ON telegram_links (telegram_user_id);
CREATE INDEX IF NOT EXISTS telegram_links_last_seen_at_idx ON telegram_links (last_seen_at DESC);

-- Google primary auth additions
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS google_id TEXT;

-- Add unique index only if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'users' AND indexname = 'users_google_id_key'
    ) THEN
        CREATE UNIQUE INDEX users_google_id_key ON users (google_id)
        WHERE google_id IS NOT NULL;
    END IF;
END$$;

-- Allow password_hash to be empty for Google-only users
ALTER TABLE users
    ALTER COLUMN password_hash SET DEFAULT '';

-- oauth_states: make user_id nullable (auth flow has no user yet)
-- and add purpose column
ALTER TABLE oauth_states
    ALTER COLUMN user_id DROP NOT NULL;

ALTER TABLE oauth_states
    ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'connect';

DROP INDEX IF EXISTS oauth_states_user_provider_expires_idx;
CREATE INDEX IF NOT EXISTS oauth_states_provider_purpose_expires_idx
    ON oauth_states (provider, purpose, expires_at);

-- Password reset tokens
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS password_reset_tokens_user_expires_idx
    ON password_reset_tokens (user_id, expires_at);

-- Yandex 360 (yax) — добавлено в патче с интеграцией yax
ALTER TABLE user_instances
    ADD COLUMN IF NOT EXISTS yax_connected    BOOLEAN     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS yax_connected_at TIMESTAMPTZ;
