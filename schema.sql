CREATE TABLE user_instances (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL UNIQUE,
    container_name TEXT NOT NULL,
    network_name   TEXT NOT NULL,
    volume_name    TEXT NOT NULL,
    telegram_bot   TEXT,
    status         TEXT NOT NULL DEFAULT 'creating',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    stopped_at     TIMESTAMPTZ
);

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
