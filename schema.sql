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
INSERT INTO settings (key, value) VALUES ('llm_model', 'nvidia/nemotron-3-super-120b-a12b:free')
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
    user_tool_use_model TEXT,
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
