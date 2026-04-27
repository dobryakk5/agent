CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO settings (key, value) VALUES ('platform', 'openrouter')
    ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('llm_model', 'nvidia/nemotron-3-super-120b-a12b:free')
    ON CONFLICT (key) DO NOTHING;
