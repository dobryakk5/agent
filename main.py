import asyncio
import asyncpg
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from docker_manager import create_instance, stop_instance, remove_instance, recreate_container
from metrics import collect_metrics_loop

load_dotenv()
DB_URL = os.environ["DATABASE_URL"]

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
        detail=f"API key is required for platform '{platform}'. "
               f"Pass it in request or set {env_name} in .env",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DB_URL)
    asyncio.create_task(collect_metrics_loop(DB_URL))
    yield
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
    telegram_bot_token: str


@app.post("/provision")
async def provision(req: ProvisionRequest):
    if req.platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform. Use: {PLATFORMS}")

    pool = app.state.pool
    existing = await pool.fetchrow(
        "SELECT status FROM user_instances WHERE user_id = $1", req.user_id
    )
    if existing:
        raise HTTPException(status_code=409, detail="Instance already exists")

    resolved_api_key = resolve_api_key(req.platform, req.api_key)

    result = create_instance(
        user_id=req.user_id,
        platform=req.platform,
        api_key=resolved_api_key,
        llm_model=req.llm_model,
        telegram_bot_token=req.telegram_bot_token,
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
        req.telegram_bot_token,
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
        "SELECT platform, api_key, telegram_bot, llm_model FROM user_instances WHERE user_id = $1",
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    resolved_api_key = resolve_api_key(
        platform=row["platform"],
        request_api_key="",
        stored_api_key=row["api_key"],
    )

    result = recreate_container(
        user_id=user_id,
        platform=row["platform"],
        api_key=resolved_api_key,
        llm_model=req.llm_model,
        telegram_bot_token=row["telegram_bot"],
    )

    await pool.execute(
        "UPDATE user_instances SET api_key=$1, llm_model=$2, status='running', stopped_at=NULL WHERE user_id=$3",
        resolved_api_key,
        req.llm_model,
        user_id,
    )

    return result


@app.post("/stop/{user_id}")
async def stop(user_id: int):
    pool = app.state.pool
    stop_instance(user_id)
    await pool.execute(
        "UPDATE user_instances SET status='stopped', stopped_at=now() WHERE user_id=$1",
        user_id,
    )
    return {"ok": True}


@app.delete("/remove/{user_id}")
async def remove(user_id: int):
    pool = app.state.pool
    remove_instance(user_id)
    await pool.execute("DELETE FROM user_instances WHERE user_id=$1", user_id)
    return {"ok": True}


@app.get("/instances")
async def list_instances():
    pool = app.state.pool
    rows = await pool.fetch("SELECT * FROM user_instances ORDER BY created_at DESC")
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