CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Миграция для пользовательских LLM-настроек в инстансах.
-- Режим отдельной tool-use model удалён: в Docker передаётся только одна базовая модель.
ALTER TABLE user_instances
    ADD COLUMN IF NOT EXISTS user_platform  TEXT,
    ADD COLUMN IF NOT EXISTS user_llm_model TEXT,
    ADD COLUMN IF NOT EXISTS user_api_key   TEXT NOT NULL DEFAULT '';

ALTER TABLE user_instances
    DROP COLUMN IF EXISTS user_tool_use_model;

INSERT INTO settings (key, value) VALUES ('platform', 'openrouter')
    ON CONFLICT (key) DO NOTHING;

INSERT INTO settings (key, value) VALUES ('llm_model', 'openrouter/free')
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    WHERE settings.value IS NULL OR settings.value = '' OR settings.value IN ('nvidia/nemotron-3-super-120b-a12b:free', 'openrouter/nvidia/nemotron-3-super-120b-a12b:free');
