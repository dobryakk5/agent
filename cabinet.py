"""
Личный кабинет пользователя.
Подключается к main.py через app.include_router(cabinet_router).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from auth import get_current_user
from docker_manager import recreate_container, stop_instance
from instance_service import PLATFORMS, resolve_api_key, sync_instance_to_admin_settings
from pydantic import BaseModel
from settings_store import get_settings
from telegram_gateway import (
    TelegramGatewayConfigError,
    create_telegram_link_token,
    unlink_telegram_account,
)

cabinet_router = APIRouter(prefix="/cabinet", tags=["cabinet"])

MODEL_OPTIONS = {
    "anthropic": [
        {
            "id": "anthropic/claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "description": "Сбалансированная модель для повседневной работы.",
        },
        {
            "id": "anthropic/claude-opus-4-6",
            "name": "Claude Opus 4.6",
            "description": "Максимум качества для сложных задач.",
        },
        {
            "id": "anthropic/claude-haiku-4-5",
            "name": "Claude Haiku 4.5",
            "description": "Быстрее и дешевле для лёгких запросов.",
        },
    ],
    "openrouter": [
        {
            "id": "openrouter/free",
            "name": "OpenRouter Free Router",
            "description": "Бесплатный роутер OpenRouter: сам выбирает доступную free-модель.",
        },
        {
            "id": "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
            "name": "Nemotron 3 Super 120B Free",
            "description": "Конкретная бесплатная модель NVIDIA через OpenRouter.",
        },
        {
            "id": "openrouter/openai/gpt-oss-120b:free",
            "name": "GPT-OSS 120B Free",
            "description": "Конкретная бесплатная OpenAI OSS-модель через OpenRouter.",
        },
    ],
    "openai": [
        {
            "id": "openai/gpt-4o",
            "name": "GPT-4o",
            "description": "Основная универсальная модель OpenAI.",
        },
        {
            "id": "openai/gpt-4o-mini",
            "name": "GPT-4o mini",
            "description": "Быстрее и дешевле для коротких задач.",
        },
        {
            "id": "openai/o3",
            "name": "o3",
            "description": "Сильнее в рассуждении и сложных цепочках.",
        },
    ],
}


class UpdateCabinetModelRequest(BaseModel):
    llm_model: str


class UpdateUserLLMRequest(BaseModel):
    platform: str = ""
    llm_model: str = ""
    api_key: str = ""


def _get_models_for_platform(platform: str | None) -> list[dict[str, str]]:
    return MODEL_OPTIONS.get((platform or "").strip(), MODEL_OPTIONS["openrouter"])


@cabinet_router.get("/page", response_class=HTMLResponse)
async def cabinet_page():
    with open("cabinet.html") as f:
        return f.read()


@cabinet_router.get("/status")
async def cabinet_status(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool

    instance = await pool.fetchrow(
        """
        SELECT container_name, status, platform, llm_model, created_at,
               stopped_at, google_connected, google_connected_at,
               user_platform, user_llm_model,
               CASE WHEN user_api_key != '' THEN true ELSE false END AS has_custom_api_key
        FROM user_instances
        WHERE user_id = $1
        """,
        user["user_id"],
    )
    telegram_link = await pool.fetchrow(
        """
        SELECT telegram_username, telegram_chat_id, linked_at, last_seen_at
        FROM telegram_links
        WHERE user_id = $1
        """,
        user["user_id"],
    )
    settings = await get_settings(pool)
    instance_dict = dict(instance) if instance else None
    active_platform = (
        (instance_dict or {}).get("user_platform")
        or (instance_dict or {}).get("platform")
        or settings.get("platform")
        or "openrouter"
    )
    model_platform = (instance_dict or {}).get("platform") or active_platform
    default_model = settings.get("llm_model") or ""

    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "instance": instance_dict,
        "telegram_link": dict(telegram_link) if telegram_link else None,
        "default_model": default_model,
        "models": _get_models_for_platform(model_platform),
        "all_models": MODEL_OPTIONS,
        "platforms": list(MODEL_OPTIONS.keys()),
        "active_platform": active_platform,
        "features": {
            "browser": {"enabled": False, "label": "Браузер", "soon": True},
            "reminders": {"enabled": False, "label": "Напоминания", "soon": True},
            "files": {"enabled": False, "label": "Файлы и документы", "soon": True},
            "memory": {"enabled": True, "label": "Память", "soon": False},
            "docker_restart": {
                "enabled": bool(instance_dict),
                "label": "Рестарт Docker",
                "soon": False,
            },
            "telegram": {
                "enabled": bool(telegram_link),
                "label": "Telegram",
                "soon": False,
            },
            "google": {
                "enabled": bool(instance and instance["google_connected"]),
                "label": "Google Workspace",
                "soon": False,
            },
        },
    }


@cabinet_router.post("/agent/stop")
async def agent_stop(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: stop_instance(user["user_id"]))
    await pool.execute(
        "UPDATE user_instances SET status='stopped', stopped_at=now() WHERE user_id=$1",
        user["user_id"],
    )
    return {"ok": True}


@cabinet_router.post("/agent/start")
async def agent_start(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool

    row = await pool.fetchrow(
        "SELECT status FROM user_instances WHERE user_id = $1",
        user["user_id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    if row["status"] == "running":
        return {"ok": True, "message": "Already running"}

    result = await sync_instance_to_admin_settings(pool, user["user_id"], force_status="running")
    return {"ok": True, **result}


@cabinet_router.post("/agent/restart")
async def agent_restart(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool
    row = await pool.fetchrow(
        """
        SELECT platform, api_key, user_api_key, gateway_token, llm_model, status
        FROM user_instances
        WHERE user_id = $1
        """,
        user["user_id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    llm_model = (row["llm_model"] or "").strip()
    if not llm_model:
        raise HTTPException(status_code=400, detail="Model is not configured for this instance")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: recreate_container(
            user_id=user["user_id"],
            platform=row["platform"],
            api_key=resolve_api_key(row["platform"], None, row["user_api_key"] or row["api_key"]),
            llm_model=llm_model,
            gateway_token=row["gateway_token"],
        ),
    )
    await pool.execute(
        """
        UPDATE user_instances
        SET status = 'running',
            stopped_at = NULL
        WHERE user_id = $1
        """,
        user["user_id"],
    )
    return {"ok": True, "status": "running"}


@cabinet_router.post("/agent/update-image")
async def agent_update_image(request: Request, user=Depends(get_current_user)):
    """Recreate only the current user's agent container from openclaw-agent:latest.

    Docker volumes and network are preserved by recreate_container() inside
    sync_instance_to_admin_settings(), so user data and secrets remain intact.
    """
    pool = request.app.state.pool

    row = await pool.fetchrow(
        """
        SELECT status
        FROM user_instances
        WHERE user_id = $1
        """,
        user["user_id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    current_status = row["status"] or "running"
    target_status = current_status if current_status in ("running", "stopped") else "running"

    result = await sync_instance_to_admin_settings(
        pool,
        user["user_id"],
        force_status=target_status,
    )

    return {
        "ok": True,
        "message": "Agent container was recreated from openclaw-agent:latest",
        **result,
    }


@cabinet_router.post("/model")
async def cabinet_update_model(
    req: UpdateCabinetModelRequest,
    request: Request,
    user=Depends(get_current_user),
):
    pool = request.app.state.pool
    row = await pool.fetchrow(
        """
        SELECT platform, api_key, gateway_token, status
        FROM user_instances
        WHERE user_id = $1
        """,
        user["user_id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    llm_model = req.llm_model.strip()
    if not llm_model:
        raise HTTPException(status_code=400, detail="Model is required")

    target_status = row["status"] if row["status"] in {"running", "stopped"} else "running"
    api_key = resolve_api_key(row["platform"], None, row["api_key"])
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: recreate_container(
            user_id=user["user_id"],
            platform=row["platform"],
            api_key=api_key,
            llm_model=llm_model,
            gateway_token=row["gateway_token"],
        ),
    )
    if target_status == "stopped":
        await loop.run_in_executor(None, lambda: stop_instance(user["user_id"]))

    await pool.execute(
        """
        UPDATE user_instances
        SET llm_model = $1,
            status = $2,
            stopped_at = CASE WHEN $2 = 'stopped' THEN now() ELSE NULL END
        WHERE user_id = $3
        """,
        llm_model,
        target_status,
        user["user_id"],
    )
    return {
        "ok": True,
        "container_id": result["container_id"],
        "llm_model": llm_model,
        "status": target_status,
    }


@cabinet_router.post("/llm")
async def cabinet_update_llm(
    req: UpdateUserLLMRequest,
    request: Request,
    user=Depends(get_current_user),
):
    pool = request.app.state.pool
    row = await pool.fetchrow(
        """
        SELECT platform, api_key, user_api_key, gateway_token, status
        FROM user_instances
        WHERE user_id = $1
        """,
        user["user_id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    new_platform = req.platform.strip() or None
    new_llm_model = req.llm_model.strip() or None
    new_user_api_key = req.api_key.strip()

    if new_platform and new_platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {new_platform}. Use one of: {sorted(PLATFORMS)}")

    settings = await get_settings(pool)
    effective_platform = new_platform or (settings.get("platform") or "openrouter")
    effective_model = new_llm_model or (settings.get("llm_model") or "")

    if not effective_model:
        raise HTTPException(status_code=400, detail="Model is required (set it in /admin or choose one here)")

    effective_api_key = resolve_api_key(
        effective_platform,
        None,
        new_user_api_key or row["user_api_key"] or row["api_key"],
    )

    target_status = row["status"] if row["status"] in {"running", "stopped"} else "running"
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: recreate_container(
            user_id=user["user_id"],
            platform=effective_platform,
            api_key=effective_api_key,
            llm_model=effective_model,
            gateway_token=row["gateway_token"],
        ),
    )
    if target_status == "stopped":
        await loop.run_in_executor(None, lambda: stop_instance(user["user_id"]))

    await pool.execute(
        """
        UPDATE user_instances
        SET user_platform = $1,
            user_llm_model = $2,
            user_api_key = $3,
            platform = $4,
            llm_model = $5,
            status = $6,
            stopped_at = CASE WHEN $6 = 'stopped' THEN now() ELSE NULL END
        WHERE user_id = $7
        """,
        new_platform,
        new_llm_model,
        new_user_api_key,
        effective_platform,
        effective_model,
        target_status,
        user["user_id"],
    )

    return {
        "ok": True,
        "container_id": result["container_id"],
        "platform": effective_platform,
        "llm_model": effective_model,
        "has_custom_api_key": bool(new_user_api_key),
        "status": target_status,
    }


@cabinet_router.post("/telegram/link/start")
async def telegram_link_start(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user["user_id"])
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    try:
        return await create_telegram_link_token(pool, user["user_id"])
    except TelegramGatewayConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@cabinet_router.delete("/telegram/link")
async def telegram_link_delete(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool
    await unlink_telegram_account(pool, user["user_id"])
    return {"ok": True}
