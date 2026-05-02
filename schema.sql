-- Пользователи (личный кабинет)
CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL DEFAULT '',
    google_id     TEXT        UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO settings (key, value) VALUES ('platform', 'openrouter')
    ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('llm_model', 'openrouter/free')
    ON CONFLICT (key) DO NOTHING;

-- Инстансы агентов
CREATE TABLE user_instances (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    container_name      TEXT NOT NULL,
    network_name        TEXT NOT NULL,
    volume_name         TEXT NOT NULL,
    secrets_volume_name TEXT NOT NULL,
    telegram_bot        TEXT,
    gateway_token       TEXT NOT NULL,
    api_key             TEXT NOT NULL DEFAULT '',
    platform            TEXT NOT NULL DEFAULT 'openrouter',
    llm_model           TEXT,
    user_platform       TEXT,
    user_llm_model      TEXT,
    user_api_key        TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'creating',
    google_connected    BOOLEAN     NOT NULL DEFAULT false,
    google_connected_at TIMESTAMPTZ,
    yax_connected       BOOLEAN     NOT NULL DEFAULT false,
    yax_connected_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    stopped_at          TIMESTAMPTZ
);

-- OAuth состояния для callback
-- user_id NULL допустим для purpose='auth' (пользователь ещё не известен)
CREATE TABLE oauth_states (
    id         UUID PRIMARY KEY,
    user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
    provider   TEXT NOT NULL,
    purpose    TEXT NOT NULL DEFAULT 'connect',
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ
);

CREATE INDEX ON oauth_states (provider, purpose, expires_at);

-- Одноразовые токены привязки Telegram
CREATE TABLE telegram_link_tokens (
    token      TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON telegram_link_tokens (user_id, expires_at);

-- Привязки Telegram -> пользователь
CREATE TABLE telegram_links (
    user_id              BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    telegram_user_id     BIGINT NOT NULL UNIQUE,
    telegram_chat_id     BIGINT NOT NULL,
    telegram_username    TEXT,
    telegram_first_name  TEXT,
    telegram_last_name   TEXT,
    linked_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON telegram_links (telegram_user_id);
CREATE INDEX ON telegram_links (last_seen_at DESC);


-- Очередь входящих Telegram updates.
-- Webhook сначала пишет сюда, а backend worker уже доставляет сообщение в Docker/OpenClaw.
CREATE TABLE telegram_updates (
    id BIGSERIAL PRIMARY KEY,

    telegram_update_id BIGINT NOT NULL UNIQUE,
    telegram_user_id BIGINT,
    chat_id BIGINT,
    message_id BIGINT,
    text TEXT,

    payload_json JSONB NOT NULL,

    status TEXT NOT NULL DEFAULT 'pending',
    -- pending | locked | checking_agent | starting_agent | waiting_gateway
    -- sending_to_agent | waiting_agent_response | sending_to_telegram
    -- done | retry | failed | rejected | ignored

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

CREATE INDEX ON telegram_updates (status, next_attempt_at);
CREATE INDEX ON telegram_updates (telegram_user_id, created_at DESC);
CREATE INDEX ON telegram_updates (user_id, created_at DESC);
CREATE INDEX ON telegram_updates (created_at DESC);

-- Последнее известное runtime-состояние пользовательского контейнера.
CREATE TABLE instance_runtime_state (
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

CREATE INDEX ON instance_runtime_state (docker_state);
CREATE INDEX ON instance_runtime_state (gateway_state);

-- Токены сброса пароля
CREATE TABLE password_reset_tokens (
    token      TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON password_reset_tokens (user_id, expires_at);

-- Метрики контейнеров
CREATE TABLE container_metrics (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL,
    container_name TEXT NOT NULL,
    cpu_percent    NUMERIC(6,2),
    mem_usage_mb   NUMERIC(10,2),
    mem_limit_mb   NUMERIC(10,2),
    net_rx_mb      NUMERIC(10,2),
    net_tx_mb      NUMERIC(10,2),
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON container_metrics (user_id, recorded_at DESC);
