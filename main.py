import asyncio
import os
from contextlib import asynccontextmanager

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, RedirectResponse
from pydantic import BaseModel

from google_oauth import build_auth_url, exchange_code_for_tokens, write_tokens_to_container
from auth import auth_router
from cabinet import cabinet_router
from google_oauth import build_auth_url, exchange_code_for_tokens, connect_google, disconnect_google
from docker_manager import (
    create_instance,
    recreate_container,
    remove_instance,
    stop_instance,
)
from metrics import collect_metrics_loop, auto_stop_loop

load_dotenv()

DB_URL = os.environ["DATABASE_URL"]

PLATFORMS = {"anthropic", "openrouter", "openai"}

PLATFORM_ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def resolve_api_key(
    platform: str,
    request_api_key: str | None,
    stored_api_key: str | None = None,
) -> str:
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


def resolve_telegram_token(
    request_token: str | None,
    stored_token: str | None = None,
) -> str:
    req_token = (request_token or "").strip()
    db_token = (stored_token or "").strip()

    if req_token:
        return req_token

    if db_token:
        return db_token

    env_token = (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
    if env_token:
        return env_token

    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DB_URL)
    app.state.metrics_task = asyncio.create_task(collect_metrics_loop(DB_URL))
    app.state.auto_stop_task = asyncio.create_task(auto_stop_loop(DB_URL))
    try:
        yield
    finally:
        for task_name in ("metrics_task", "auto_stop_task"):
            task = getattr(app.state, task_name, None)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await app.state.pool.close()


app = FastAPI(lifespan=lifespan)

app.include_router(auth_router)
app.include_router(cabinet_router)


@app.get("/")
async def dashboard():
    return FileResponse("cabinet.html")


@app.get("/admin")
async def admin_dashboard():
    return FileResponse("dashboard.html")


class ProvisionRequest(BaseModel):
    user_id: int
    platform: str
    api_key: str = ""
    llm_model: str
    telegram_bot_token: str = ""


@app.post("/provision")
async def provision(req: ProvisionRequest):
    if req.platform not in PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown platform. Use one of: {sorted(PLATFORMS)}",
        )

    pool = app.state.pool

    existing = await pool.fetchrow(
        """
        SELECT user_id, status
        FROM user_instances
        WHERE user_id = $1
        """,
        req.user_id,
    )
    if existing:
        raise HTTPException(status_code=409, detail="Instance already exists")

    resolved_api_key = resolve_api_key(req.platform, req.api_key)
    resolved_tg_token = resolve_telegram_token(req.telegram_bot_token)

    result = create_instance(
        user_id=req.user_id,
        platform=req.platform,
        api_key=resolved_api_key,
        llm_model=req.llm_model,
        telegram_bot_token=resolved_tg_token,
    )

    await pool.execute(
        """
        INSERT INTO user_instances
            (user_id, container_name, network_name, volume_name,
             telegram_bot, api_key, platform, llm_model, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'running')
        """,
        req.user_id,
        result["container_name"],
        result["network_name"],
        result["volume_name"],
        resolved_tg_token,
        resolved_api_key,
        req.platform,
        req.llm_model,
    )

    return result


class UpdateModelRequest(BaseModel):
    llm_model: str


@app.post("/update/{user_id}")
async def update_model(user_id: int, req: UpdateModelRequest):
    pool = app.state.pool

    row = await pool.fetchrow(
        """
        SELECT platform, api_key, telegram_bot, llm_model
        FROM user_instances
        WHERE user_id = $1
        """,
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    platform = row["platform"]
    stored_api_key = row["api_key"]
    stored_tg_token = row["telegram_bot"]

    resolved_api_key = resolve_api_key(
        platform=platform,
        request_api_key="",
        stored_api_key=stored_api_key,
    )
    resolved_tg_token = resolve_telegram_token(
        request_token="",
        stored_token=stored_tg_token,
    )

    result = recreate_container(
        user_id=user_id,
        platform=platform,
        api_key=resolved_api_key,
        llm_model=req.llm_model,
        telegram_bot_token=resolved_tg_token,
    )

    await pool.execute(
        """
        UPDATE user_instances
        SET api_key = $1,
            telegram_bot = $2,
            llm_model = $3,
            status = 'running',
            stopped_at = NULL
        WHERE user_id = $4
        """,
        resolved_api_key,
        resolved_tg_token,
        req.llm_model,
        user_id,
    )

    return result


@app.post("/stop/{user_id}")
async def stop(user_id: int):
    pool = app.state.pool

    row = await pool.fetchrow(
        "SELECT user_id FROM user_instances WHERE user_id = $1",
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    stop_instance(user_id)

    await pool.execute(
        """
        UPDATE user_instances
        SET status = 'stopped',
            stopped_at = now()
        WHERE user_id = $1
        """,
        user_id,
    )

    return {"ok": True}


@app.delete("/remove/{user_id}")
async def remove(user_id: int):
    pool = app.state.pool

    row = await pool.fetchrow(
        "SELECT user_id FROM user_instances WHERE user_id = $1",
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    remove_instance(user_id)
    await pool.execute("DELETE FROM user_instances WHERE user_id = $1", user_id)

    return {"ok": True}


@app.get("/instances")
async def list_instances():
    pool = app.state.pool
    rows = await pool.fetch(
        """
        SELECT *
        FROM user_instances
        ORDER BY created_at DESC
        """
    )
    return [dict(r) for r in rows]



# ─── Google OAuth ──────────────────────────────────────────────────────────────

@app.get("/oauth/google/start/{user_id}")
async def google_oauth_start(user_id: int):
    """Редиректим пользователя на Google для авторизации."""
    pool = app.state.pool
    row = await pool.fetchrow(
        "SELECT user_id FROM user_instances WHERE user_id = $1", user_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    url = build_auth_url(user_id)
    return RedirectResponse(url)


@app.get("/oauth/google/callback")
async def google_oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Google редиректит сюда после авторизации пользователя."""
    if error:
        return {"error": error, "detail": "Google OAuth denied or failed"}

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    try:
        user_id = int(state)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid state (user_id)")

    # Меняем code на токены
    tokens = await exchange_code_for_tokens(code)

    # Сохраняем в БД для истории и возможного пересоздания контейнера
    pool = app.state.pool
    await pool.execute(
        """
        UPDATE user_instances
        SET google_connected = true,
            google_connected_at = now()
        WHERE user_id = $1
        """,
        user_id,
    )

    # Кладём токены прямо в контейнер
    write_tokens_to_container(user_id, tokens)

    return {
        "ok": True,
        "user_id": user_id,
        "message": "Google connected. Agent now has access to Gmail, Calendar and Drive.",
        "scopes": tokens.get("scope", ""),
    }


@app.delete("/oauth/google/{user_id}")
async def google_oauth_disconnect(user_id: int):
    """Отключаем Google — удаляем токены из контейнера."""
    import docker
    client = docker.from_env()
    container_name = f"agent_user_{user_id}"

    try:
        container = client.containers.get(container_name)
        container.exec_run("rm -f /root/.openclaw/secrets/google-tokens.json")

        # Деактивируем плагин в конфиге
        deactivate_script = """
import json
path = '/root/.openclaw/openclaw.json'
with open(path) as f:
    c = json.load(f)
entries = c.get('plugins', {}).get('entries', {})
if 'openclaw-google-workspace' in entries:
    entries['openclaw-google-workspace']['enabled'] = False
with open(path, 'w') as f:
    json.dump(c, f, indent=2)
print('ok')
"""
        container.exec_run(["python3", "-c", deactivate_script])
    except docker.errors.NotFound:
        pass

    pool = app.state.pool
    await pool.execute(
        "UPDATE user_instances SET google_connected = false, google_connected_at = NULL WHERE user_id = $1",
        user_id,
    )

    return {"ok": True}



# ─── Google OAuth ──────────────────────────────────────────────────────────────

@app.get("/oauth/google/start/{user_id}")
async def google_oauth_start(user_id: int, request: Request):
    pool = request.app.state.pool
    row = await pool.fetchrow(
        "SELECT user_id FROM user_instances WHERE user_id = $1", user_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    return RedirectResponse(build_auth_url(user_id))


@app.get("/oauth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    if error:
        return {"error": error}
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")
    try:
        user_id = int(state)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid state")

    tokens = await exchange_code_for_tokens(code)
    connect_google(user_id, tokens)

    pool = request.app.state.pool
    await pool.execute(
        "UPDATE user_instances SET google_connected=true, google_connected_at=now() WHERE user_id=$1",
        user_id,
    )
    # Редиректим обратно в кабинет
    return RedirectResponse("/?google=connected")


@app.delete("/oauth/google/{user_id}")
async def google_oauth_disconnect(user_id: int, request: Request):
    disconnect_google(user_id)
    pool = request.app.state.pool
    await pool.execute(
        "UPDATE user_instances SET google_connected=false, google_connected_at=NULL WHERE user_id=$1",
        user_id,
    )
    return {"ok": True}


@app.get("/metrics/{user_id}")
async def get_metrics(user_id: int):
    pool = app.state.pool
    rows = await pool.fetch(
        """
        SELECT cpu_percent, mem_usage_mb, net_rx_mb, net_tx_mb, recorded_at
        FROM container_metrics
        WHERE user_id = $1
        ORDER BY recorded_at DESC
        LIMIT 50
        """,
        user_id,
    )
    return [dict(r) for r in rows]
