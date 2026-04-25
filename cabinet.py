"""
Личный кабинет пользователя.
Подключается к main.py через app.include_router(cabinet_router).
"""

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from auth import get_current_user
from docker_manager import recreate_container, start_instance, stop_instance
from telegram_gateway import (
    TelegramGatewayConfigError,
    create_telegram_link_token,
    unlink_telegram_account,
)

cabinet_router = APIRouter(prefix="/cabinet", tags=["cabinet"])

DEFAULT_LLM_MODEL = os.environ.get("DEFAULT_LLM_MODEL", "openrouter/meta-llama/llama-3.3-70b-instruct:free")

AVAILABLE_MODELS = [
    {"id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",       "name": "Llama 3.3 70B",         "description": "Быстрый, хорош для чата"},
    {"id": "openrouter/deepseek/deepseek-chat-v3-0324:free",           "name": "DeepSeek Chat V3",      "description": "Умный, хорош для задач"},
    {"id": "openrouter/deepseek/deepseek-r1:free",                     "name": "DeepSeek R1 (reasoning)","description": "Медленный, но думает глубоко"},
    {"id": "openrouter/nvidia/nemotron-3-super-120b-a12b:free",        "name": "Nemotron 120B",         "description": "Большая модель NVIDIA"},
    {"id": "openrouter/qwen/qwen3-235b-a22b:free",                     "name": "Qwen3 235B",            "description": "Мощная китайская модель"},
    {"id": "openrouter/moonshotai/kimi-k2:free",                       "name": "Kimi K2",               "description": "Агентная модель, хороша для задач"},
    {"id": "openrouter/openai/gpt-oss-120b:free",                      "name": "GPT OSS 120B",          "description": "Open-source модель OpenAI"},
    {"id": "openrouter/mistralai/devstral-small:free",                 "name": "Devstral Small",        "description": "Специализирован на коде"},
]


class UpdateModelRequest(BaseModel):
    llm_model: str


@cabinet_router.get("/page", response_class=HTMLResponse)
async def cabinet_page():
    with open("cabinet.html") as f:
        return f.read()


@cabinet_router.get("/status")
async def cabinet_status(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool

    instance = await pool.fetchrow(
        """
        SELECT container_name, llm_model, status, created_at,
               stopped_at, google_connected, google_connected_at
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

    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "instance": dict(instance) if instance else None,
        "telegram_link": dict(telegram_link) if telegram_link else None,
        "models": AVAILABLE_MODELS,
        "default_model": DEFAULT_LLM_MODEL,
        "features": {
            "browser": {"enabled": False, "label": "Браузер", "soon": True},
            "reminders": {"enabled": False, "label": "Напоминания", "soon": True},
            "files": {"enabled": False, "label": "Файлы и документы", "soon": True},
            "memory": {"enabled": True, "label": "Память", "soon": False},
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


@cabinet_router.post("/model")
async def update_model(req: UpdateModelRequest, request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool
    allowed_ids = {m["id"] for m in AVAILABLE_MODELS}
    if req.llm_model not in allowed_ids:
        raise HTTPException(status_code=400, detail="Model not available")

    row = await pool.fetchrow(
        "SELECT platform, api_key, gateway_token, status FROM user_instances WHERE user_id = $1",
        user["user_id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: recreate_container(
            user_id=user["user_id"],
            platform=row["platform"],
            api_key=row["api_key"],
            llm_model=req.llm_model,
            gateway_token=row["gateway_token"],
        ),
    )

    await pool.execute(
        "UPDATE user_instances SET llm_model = $1, status = 'running', stopped_at = NULL WHERE user_id = $2",
        req.llm_model,
        user["user_id"],
    )

    return {"ok": True, "llm_model": req.llm_model, "container_id": result["container_id"]}


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
        "SELECT platform, api_key, gateway_token, llm_model, status FROM user_instances WHERE user_id = $1",
        user["user_id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    if row["status"] == "running":
        return {"ok": True, "message": "Already running"}

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: start_instance(user["user_id"]))

    await pool.execute(
        "UPDATE user_instances SET status='running', stopped_at=NULL WHERE user_id=$1",
        user["user_id"],
    )

    return {"ok": True}


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
