-- Миграция для пользовательских LLM-настроек в инстансах
ALTER TABLE user_instances
    ADD COLUMN IF NOT EXISTS user_platform       TEXT,
    ADD COLUMN IF NOT EXISTS user_llm_model      TEXT,
    ADD COLUMN IF NOT EXISTS user_api_key        TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS user_tool_use_model TEXT;
