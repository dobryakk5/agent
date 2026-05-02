import asyncio
import os

from fastapi import HTTPException

from docker_manager import recreate_container, stop_instance
from settings_store import get_settings
from runtime_state import refresh_instance_runtime_state_safe

PLATFORMS = {"anthropic", "openrouter", "openai"}
PLATFORM_ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def resolve_api_key(platform: str, request_api_key: str | None, stored_api_key: str | None = None) -> str:
    req_key = (request_api_key or "").strip()
    db_key = (stored_api_key or "").strip()
    if req_key:
        return req_key
    if db_key:
        return db_key
    env_name = PLATFORM_ENV_KEYS.get(platform)
    env_key = (os.getenv(env_name, "") or "").strip() if env_name else ""
    if env_key:
        return env_key
    raise HTTPException(
        status_code=400,
        detail=(
            f"API key is required for platform '{platform}'. "
            f"Pass it in request or set {env_name} in .env"
        ),
    )


async def sync_instance_to_admin_settings(
    pool,
    user_id: int,
    *,
    force_status: str | None = None,
) -> dict[str, str]:
    row = await pool.fetchrow(
        """
        SELECT user_id, api_key, user_api_key, gateway_token, status,
               user_platform, user_llm_model
        FROM user_instances
        WHERE user_id = $1
        """,
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    settings = await get_settings(pool)
    platform = (row["user_platform"] or "").strip() or (settings.get("platform") or "").strip()
    llm_model = (row["user_llm_model"] or "").strip() or (settings.get("llm_model") or "").strip()
    if not platform or not llm_model:
        raise HTTPException(status_code=400, detail="Admin settings are incomplete. Set platform and model in /admin")
    target_status = force_status or row["status"] or "running"
    api_key = resolve_api_key(platform, None, row["user_api_key"] or row["api_key"])

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: recreate_container(
            user_id=user_id,
            platform=platform,
            api_key=api_key,
            llm_model=llm_model,
            gateway_token=row["gateway_token"],
        ),
    )

    if target_status == "stopped":
        await loop.run_in_executor(None, lambda: stop_instance(user_id))

    await pool.execute(
        """
        UPDATE user_instances
        SET platform = $1,
            llm_model = $2,
            status = $3,
            stopped_at = CASE WHEN $3 = 'stopped' THEN now() ELSE NULL END
        WHERE user_id = $4
        """,
        platform,
        llm_model,
        target_status,
        user_id,
    )
    await refresh_instance_runtime_state_safe(
        pool,
        user_id,
        gateway_state="stopped" if target_status == "stopped" else "starting",
    )

    return {
        "container_id": result["container_id"],
        "platform": platform,
        "llm_model": llm_model,
        "status": target_status,
    }


async def apply_admin_settings_to_all_instances(pool) -> dict:
    rows = await pool.fetch(
        """
        SELECT user_id
        FROM user_instances
        ORDER BY created_at DESC
        """
    )

    applied = []
    failed = []
    for row in rows:
        user_id = row["user_id"]
        try:
            result = await sync_instance_to_admin_settings(pool, user_id)
            applied.append(
                {
                    "user_id": user_id,
                    "platform": result["platform"],
                    "llm_model": result["llm_model"],
                    "status": result["status"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            failed.append({"user_id": user_id, "error": str(exc)})

    return {
        "ok": not failed,
        "applied_count": len(applied),
        "failed_count": len(failed),
        "applied": applied,
        "failed": failed,
    }
