import asyncio
import os
from contextlib import asynccontextmanager

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from docker_manager import (
    create_instance,
    recreate_container,
    remove_instance,
    stop_instance,
)
from metrics import collect_metrics_loop

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
    try:
        yield
    finally:
        task = getattr(app.state, "metrics_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def dashboard():
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