import asyncio
import asyncpg
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from docker_manager import create_instance, stop_instance, remove_instance
from metrics import collect_metrics_loop

load_dotenv()
DB_URL = os.environ["DATABASE_URL"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DB_URL)
    asyncio.create_task(collect_metrics_loop(DB_URL))
    yield
    await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/")
async def dashboard():
    return FileResponse("dashboard.html")


# ─── API ──────────────────────────────────────────────────────────────────────

class ProvisionRequest(BaseModel):
    user_id: int
    api_key: str
    telegram_bot_token: str


@app.post("/provision")
async def provision(req: ProvisionRequest):
    pool = app.state.pool

    existing = await pool.fetchrow(
        "SELECT status FROM user_instances WHERE user_id = $1", req.user_id
    )
    if existing:
        raise HTTPException(status_code=409, detail="Instance already exists")

    result = create_instance(req.user_id, req.api_key, req.telegram_bot_token)

    await pool.execute(
        """
        INSERT INTO user_instances
            (user_id, container_name, network_name, volume_name, telegram_bot, status)
        VALUES ($1, $2, $3, $4, $5, 'running')
        """,
        req.user_id,
        result["container_name"],
        result["network_name"],
        result["volume_name"],
        req.telegram_bot_token,
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


@app.get("/metrics/{user_id}/latest")
async def get_latest_metric(user_id: int):
    pool = app.state.pool
    row = await pool.fetchrow(
        """
        SELECT cpu_percent, mem_usage_mb, net_rx_mb, net_tx_mb, recorded_at
        FROM container_metrics
        WHERE user_id = $1
        ORDER BY recorded_at DESC
        LIMIT 1
        """,
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No metrics yet")
    return dict(row)
