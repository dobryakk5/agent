-- Миграция для существующей БД
-- psql $DATABASE_URL -f migrate_users.sql

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE user_instances
    ADD COLUMN IF NOT EXISTS google_connected    BOOLEAN     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS google_connected_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS api_key             TEXT        NOT NULL DEFAULT '';

-- Добавляем foreign key если ещё нет
-- (выполнять только если user_id в user_instances совпадает с users.id)
-- ALTER TABLE user_instances ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
