DEFAULT_PLATFORM = "openrouter"
DEFAULT_LLM_MODEL = "openrouter/free"


async def ensure_settings_defaults(pool) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    await pool.execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('platform', $1)
        ON CONFLICT (key) DO NOTHING
        """,
        DEFAULT_PLATFORM,
    )
    await pool.execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('llm_model', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        WHERE settings.value IS NULL
           OR settings.value = ''
           OR settings.value IN ('nvidia/nemotron-3-super-120b-a12b:free', 'openrouter/nvidia/nemotron-3-super-120b-a12b:free')
        """,
        DEFAULT_LLM_MODEL,
    )


async def get_settings(pool) -> dict[str, str]:
    await ensure_settings_defaults(pool)
    rows = await pool.fetch("SELECT key, value FROM settings")
    return {row["key"]: row["value"] for row in rows}


async def write_settings(pool, platform: str, llm_model: str) -> dict[str, str]:
    await ensure_settings_defaults(pool)
    await pool.execute(
        "INSERT INTO settings (key, value) VALUES ('platform', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        platform,
    )
    await pool.execute(
        "INSERT INTO settings (key, value) VALUES ('llm_model', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        llm_model,
    )
    return {"platform": platform, "llm_model": llm_model}
