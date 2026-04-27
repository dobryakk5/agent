"""
Личный кабинет пользователя.
Подключается к main.py через app.include_router(cabinet_router).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from auth import get_current_user
from docker_manager import stop_instance
from instance_service import sync_instance_to_admin_settings
from telegram_gateway import (
    TelegramGatewayConfigError,
    create_telegram_link_token,
    unlink_telegram_account,
)

cabinet_router = APIRouter(prefix="/cabinet", tags=["cabinet"])


@cabinet_router.get("/page", response_class=HTMLResponse)
async def cabinet_page():
    with open("cabinet.html") as f:
        return f.read()


@cabinet_router.get("/status")
async def cabinet_status(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool

    instance = await pool.fetchrow(
        """
        SELECT container_name, status, created_at,
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
