CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO settings (key, value) VALUES ('platform', 'openrouter')
    ON CONFLICT (key) DO NOTHING;

INSERT INTO settings (key, value) VALUES ('llm_model', 'openrouter/free')
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    WHERE settings.value IS NULL
       OR settings.value = ''
       OR settings.value IN (
            'nvidia/nemotron-3-super-120b-a12b:free',
            'openrouter/nvidia/nemotron-3-super-120b-a12b:free'
       );
