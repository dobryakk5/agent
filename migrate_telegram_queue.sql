-- Durable Telegram queue and Docker/Gateway runtime state.
-- Run:
--   sudo -u postgres psql openclaw_admin -f migrate_telegram_queue.sql

CREATE TABLE IF NOT EXISTS telegram_updates (
    id BIGSERIAL PRIMARY KEY,

    telegram_update_id BIGINT NOT NULL UNIQUE,
    telegram_user_id BIGINT,
    chat_id BIGINT,
    message_id BIGINT,
    text TEXT,

    payload_json JSONB NOT NULL,

    status TEXT NOT NULL DEFAULT 'pending',

    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    instance_id BIGINT REFERENCES user_instances(id) ON DELETE SET NULL,

    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at TIMESTAMPTZ,

    agent_started_message_sent BOOLEAN NOT NULL DEFAULT false,
    agent_slow_message_sent BOOLEAN NOT NULL DEFAULT false,
    agent_unavailable_message_sent BOOLEAN NOT NULL DEFAULT false,
    admin_check_message_sent BOOLEAN NOT NULL DEFAULT false,

    sent_to_agent_at TIMESTAMPTZ,
    agent_response_at TIMESTAMPTZ,
    telegram_response_at TIMESTAMPTZ,

    agent_response_text TEXT,
    last_error TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Safe if an older partial migration already created the table.
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS chat_id BIGINT;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS message_id BIGINT;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS text TEXT;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS payload_json JSONB;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS instance_id BIGINT REFERENCES user_instances(id) ON DELETE SET NULL;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS agent_started_message_sent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS agent_slow_message_sent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS agent_unavailable_message_sent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS admin_check_message_sent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS sent_to_agent_at TIMESTAMPTZ;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS agent_response_at TIMESTAMPTZ;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS telegram_response_at TIMESTAMPTZ;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS agent_response_text TEXT;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE telegram_updates ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Resolve ownership for already-saved queue rows if the Telegram account is linked.
UPDATE telegram_updates u
SET user_id = l.user_id,
    updated_at = now()
FROM telegram_links l
WHERE u.user_id IS NULL
  AND u.telegram_user_id = l.telegram_user_id;

CREATE INDEX IF NOT EXISTS telegram_updates_status_next_attempt_idx
    ON telegram_updates (status, next_attempt_at);

CREATE INDEX IF NOT EXISTS telegram_updates_telegram_user_created_idx
    ON telegram_updates (telegram_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS telegram_updates_user_created_idx
    ON telegram_updates (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS telegram_updates_created_idx
    ON telegram_updates (created_at DESC);

CREATE TABLE IF NOT EXISTS instance_runtime_state (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    container_name TEXT,
    container_id TEXT,

    docker_exists BOOLEAN NOT NULL DEFAULT false,
    docker_state TEXT NOT NULL DEFAULT 'unknown',
    docker_started BOOLEAN NOT NULL DEFAULT false,
    docker_has_ip BOOLEAN NOT NULL DEFAULT false,
    docker_ip TEXT,

    gateway_state TEXT NOT NULL DEFAULT 'unknown',
    gateway_ready BOOLEAN NOT NULL DEFAULT false,

    last_checked_at TIMESTAMPTZ,
    last_started_at TIMESTAMPTZ,
    last_ready_at TIMESTAMPTZ,
    last_error TEXT,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS container_name TEXT;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS container_id TEXT;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS docker_exists BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS docker_state TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS docker_started BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS docker_has_ip BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS docker_ip TEXT;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS gateway_state TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS gateway_ready BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS last_started_at TIMESTAMPTZ;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS last_ready_at TIMESTAMPTZ;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE instance_runtime_state ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS instance_runtime_state_docker_state_idx
    ON instance_runtime_state (docker_state);

CREATE INDEX IF NOT EXISTS instance_runtime_state_gateway_state_idx
    ON instance_runtime_state (gateway_state);
