import asyncio
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import asyncpg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

# load_dotenv MUST run before any local imports so that module-level
# os.environ.get() calls in telegram_gateway, google_oauth, docker_manager
# see the values from the .env file.
load_dotenv()

from auth import auth_router, get_admin_user, get_current_user
from cabinet import cabinet_router
from docker_manager import (
    create_instance,
    delete_user_secret_file,
    recreate_container,
    remove_instance,
    stop_instance,
    write_user_secret_json,
)
from google_oauth import GoogleOAuthConfigError, build_auth_url, exchange_code_for_tokens, get_google_userinfo
from yandex_oauth import YandexOAuthConfigError, build_yandex_auth_url, exchange_yandex_code
from metrics import auto_stop_loop, collect_metrics_loop
from telegram_gateway import (
    TelegramGatewayConfigError,
    consume_telegram_link_token,
    find_user_by_telegram_id,
    get_telegram_webhook_info,
    route_telegram_message_to_instance,
    send_telegram_text,
    set_telegram_webhook,
    unlink_telegram_account,
    update_telegram_presence,
    upsert_telegram_link,
    verify_telegram_secret,
)

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
        detail=(
            f"API key is required for platform '{platform}'. "
            f"Pass it in request or set {env_name} in .env"
        ),
    )


def generate_gateway_token() -> str:
    return secrets.token_urlsafe(32)


async def create_google_state(pool, user_id: int | None, purpose: str = "connect") -> str:
    """Create an oauth_states row.

    purpose='connect' — user already logged in, wants to link Google Workspace.
                        user_id is required.
    purpose='auth'    — unauthenticated login/registration via Google.
                        user_id is None.
    """
    state_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    await pool.execute(
        """
        INSERT INTO oauth_states (id, user_id, provider, purpose, expires_at)
        VALUES ($1, $2, 'google', $3, $4)
        """,
        state_id,
        user_id,
        purpose,
        expires_at,
    )
    return state_id


async def consume_google_state(pool, state_id: str) -> dict:
    """Mark state as used and return {user_id, purpose}.

    user_id may be None for purpose='auth'.
    """
    row = await pool.fetchrow(
        """
        UPDATE oauth_states
        SET used_at = now()
        WHERE id = $1
          AND provider = 'google'
          AND used_at IS NULL
          AND expires_at > now()
        RETURNING user_id, purpose
        """,
        state_id,
    )
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    return {"user_id": row["user_id"], "purpose": row["purpose"]}


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


class UpdateModelRequest(BaseModel):
    llm_model: str


@app.post("/provision")
async def provision(req: ProvisionRequest, request: Request, admin=Depends(get_admin_user)):
    if req.platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform. Use one of: {sorted(PLATFORMS)}")

    pool = request.app.state.pool
    existing = await pool.fetchrow("SELECT user_id, status FROM user_instances WHERE user_id = $1", req.user_id)
    if existing:
        raise HTTPException(status_code=409, detail="Instance already exists")

    resolved_api_key = resolve_api_key(req.platform, req.api_key)
    gateway_token = generate_gateway_token()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: create_instance(
            user_id=req.user_id,
            platform=req.platform,
            api_key=resolved_api_key,
            llm_model=req.llm_model,
            gateway_token=gateway_token,
        ),
    )

    await pool.execute(
        """
        INSERT INTO user_instances
            (user_id, container_name, network_name, volume_name, secrets_volume_name,
             telegram_bot, gateway_token, api_key, platform, llm_model, status)
        VALUES ($1, $2, $3, $4, $5, '', $6, $7, $8, $9, 'running')
        """,
        req.user_id,
        result["container_name"],
        result["network_name"],
        result["volume_name"],
        result["secrets_volume_name"],
        gateway_token,
        resolved_api_key,
        req.platform,
        req.llm_model,
    )
    return result


@app.post("/update/{user_id}")
async def update_model(user_id: int, req: UpdateModelRequest, request: Request, admin=Depends(get_admin_user)):
    pool = request.app.state.pool
    row = await pool.fetchrow(
        "SELECT platform, api_key, gateway_token FROM user_instances WHERE user_id = $1",
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: recreate_container(
            user_id=user_id,
            platform=row["platform"],
            api_key=resolve_api_key(row["platform"], None, row["api_key"]),
            llm_model=req.llm_model,
            gateway_token=row["gateway_token"],
        ),
    )

    await pool.execute(
        """
        UPDATE user_instances
        SET llm_model = $1,
            status = 'running',
            stopped_at = NULL
        WHERE user_id = $2
        """,
        req.llm_model,
        user_id,
    )
    return result


@app.post("/stop/{user_id}")
async def stop(user_id: int, request: Request, admin=Depends(get_admin_user)):
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: stop_instance(user_id))
    await pool.execute("UPDATE user_instances SET status = 'stopped', stopped_at = now() WHERE user_id = $1", user_id)
    return {"ok": True}


@app.delete("/remove/{user_id}")
async def remove(user_id: int, request: Request, admin=Depends(get_admin_user)):
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: remove_instance(user_id))
    await pool.execute("DELETE FROM user_instances WHERE user_id = $1", user_id)
    await pool.execute("DELETE FROM telegram_links WHERE user_id = $1", user_id)
    return {"ok": True}


@app.get("/instances")
async def list_instances(request: Request, admin=Depends(get_admin_user)):
    pool = request.app.state.pool
    rows = await pool.fetch(
        """
        SELECT i.user_id, i.container_name, i.network_name, i.volume_name, i.secrets_volume_name,
               i.platform, i.llm_model, i.status, i.google_connected, i.google_connected_at,
               i.created_at, i.stopped_at,
               l.telegram_user_id, l.telegram_username, l.last_seen_at
        FROM user_instances i
        LEFT JOIN telegram_links l ON l.user_id = i.user_id
        ORDER BY i.created_at DESC
        """
    )
    return [dict(r) for r in rows]


@app.get("/auth/google/start")
async def google_auth_start(request: Request):
    """Start Google OAuth for login/registration — no JWT required."""
    pool = request.app.state.pool
    state = await create_google_state(pool, user_id=None, purpose="auth")
    try:
        url = build_auth_url(state)
    except GoogleOAuthConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return RedirectResponse(url)


@app.get("/oauth/google/start")
async def google_oauth_start(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user["user_id"])
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    state = await create_google_state(pool, user_id=user["user_id"], purpose="connect")
    try:
        url = build_auth_url(state)
    except GoogleOAuthConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return RedirectResponse(url)


@app.post("/oauth/google/start")
async def google_oauth_start_json(request: Request, user=Depends(get_current_user)):
    """Start Google OAuth connect flow from cabinet via authenticated fetch.

    A direct browser navigation to GET /oauth/google/start cannot include the
    JWT stored in localStorage, so cabinet.html calls this endpoint with an
    Authorization header and then redirects the browser to the returned Google
    OAuth URL.
    """
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user["user_id"])
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    state = await create_google_state(pool, user_id=user["user_id"], purpose="connect")
    try:
        url = build_auth_url(state)
    except GoogleOAuthConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"url": url}


@app.get("/oauth/google/callback")
async def google_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse("/cabinet/page?google=denied")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    pool = request.app.state.pool
    state_data = await consume_google_state(pool, state)
    purpose = state_data["purpose"]

    try:
        tokens = await exchange_code_for_tokens(code)
    except GoogleOAuthConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Google token exchange failed: {exc}") from exc

    # ── Auth flow: login or register via Google ────────────────────────────
    if purpose == "auth":
        try:
            userinfo = await get_google_userinfo(tokens["access_token"])
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not fetch Google user info: {exc}") from exc

        google_id = userinfo.get("sub")
        google_email = (userinfo.get("email") or "").lower().strip()

        if not google_id or not google_email:
            raise HTTPException(status_code=400, detail="Google account has no email")

        if not userinfo.get("email_verified"):
            raise HTTPException(status_code=400, detail="Google email is not verified")

        # Find existing user: first by google_id, then by email
        user_row = await pool.fetchrow(
            "SELECT id, email FROM users WHERE google_id = $1", google_id
        )
        if not user_row:
            user_row = await pool.fetchrow(
                "SELECT id, email FROM users WHERE email = $1", google_email
            )
            if user_row:
                # Existing email/password user — attach google_id
                await pool.execute(
                    "UPDATE users SET google_id = $1 WHERE id = $2",
                    google_id, user_row["id"],
                )
            else:
                # New user — create without password
                user_id = await pool.fetchval(
                    """
                    INSERT INTO users (email, password_hash, google_id)
                    VALUES ($1, '', $2)
                    RETURNING id
                    """,
                    google_email,
                    google_id,
                )
                user_row = {"id": user_id, "email": google_email}

        from auth import create_token
        jwt_token = create_token(user_row["id"], user_row["email"])
        # Pass token via query param; cabinet.html init() picks it up,
        # stores to localStorage and strips it from the URL.
        from urllib.parse import quote as urlquote
        return RedirectResponse(
            f"/?google_token={urlquote(jwt_token)}&google_email={urlquote(user_row['email'])}"
        )

    # ── Connect flow: link Google Workspace to existing account ───────────
    user_id = state_data["user_id"]
    if user_id is None:
        raise HTTPException(status_code=400, detail="State has no user_id for connect flow")

    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: write_user_secret_json(user_id, "google-tokens.json", tokens)
    )
    await pool.execute(
        "UPDATE user_instances SET google_connected = true, google_connected_at = now() WHERE user_id = $1",
        user_id,
    )
    return RedirectResponse("/cabinet/page?google=connected")


@app.delete("/oauth/google")
async def google_oauth_disconnect(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user["user_id"])
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: delete_user_secret_file(user["user_id"], "google-tokens.json")
    )
    await pool.execute(
        "UPDATE user_instances SET google_connected = false, google_connected_at = NULL WHERE user_id = $1",
        user["user_id"],
    )
    return {"ok": True}



# ── Yandex 360 OAuth ──────────────────────────────────────────────────────────

@app.get("/yandex/oauth/start")
async def yandex_oauth_start(request: Request, user=Depends(get_current_user)):
    """Начать подключение Yandex 360 к инстансу агента."""
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user["user_id"])
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    state = await create_google_state(pool, user_id=user["user_id"], purpose="yandex_connect")
    try:
        url = build_yandex_auth_url(state)
    except YandexOAuthConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return RedirectResponse(url)


@app.get("/yandex/oauth/callback")
async def yandex_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse("/cabinet/page?yandex=denied")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    pool = request.app.state.pool
    state_data = await consume_google_state(pool, state)  # переиспользуем oauth_states
    if state_data["purpose"] != "yandex_connect":
        raise HTTPException(status_code=400, detail="Invalid OAuth state purpose")

    user_id = state_data["user_id"]
    if user_id is None:
        raise HTTPException(status_code=400, detail="State has no user_id")

    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    try:
        tokens = await exchange_yandex_code(code)
    except YandexOAuthConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Yandex token exchange failed: {exc}") from exc

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: write_user_secret_json(user_id, "yax-token.json", tokens)
    )
    await pool.execute(
        "UPDATE user_instances SET yax_connected = true, yax_connected_at = now() WHERE user_id = $1",
        user_id,
    )
    return RedirectResponse("/cabinet/page?yandex=connected")


@app.delete("/yandex/oauth")
async def yandex_oauth_disconnect(request: Request, user=Depends(get_current_user)):
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT user_id FROM user_instances WHERE user_id = $1", user["user_id"])
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: delete_user_secret_file(user["user_id"], "yax-token.json")
    )
    await pool.execute(
        "UPDATE user_instances SET yax_connected = false, yax_connected_at = NULL WHERE user_id = $1",
        user["user_id"],
    )
    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not verify_telegram_secret(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")

    update = await request.json()
    # Process in the background so Telegram gets 200 OK immediately.
    # Without this, the webhook handler could block for up to 225 s
    # (45 s container readiness wait + 180 s agent response), causing
    # Telegram to retry and deliver the same message multiple times.
    asyncio.create_task(_process_telegram_update(request.app.state.pool, update))
    return {"ok": True}


async def _process_telegram_update(pool, update: dict) -> None:
    try:
        await _handle_telegram_update(pool, update)
    except Exception as exc:
        print(f"[telegram] unhandled error in background update handler: {exc}")


async def _handle_telegram_update(pool, update: dict) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text = (message.get("text") or "").strip()
    chat_id = int(chat.get("id") or 0)
    telegram_user_id = int(sender.get("id") or 0)
    reply_to_message_id = message.get("message_id")

    if chat.get("type") != "private":
        return

    if text.startswith("/start"):
        arg = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        if arg.startswith("link_"):
            token = arg[5:]
            try:
                user_id = await consume_telegram_link_token(pool, token)
                await upsert_telegram_link(pool, user_id, message)
                await send_telegram_text(chat_id, "Telegram успешно привязан к вашему ассистенту.", reply_to_message_id)
            except HTTPException:
                await send_telegram_text(chat_id, "Ссылка привязки недействительна или уже использована.", reply_to_message_id)
            except Exception as exc:
                print(f"[telegram] error during link: {exc}")
        else:
            try:
                await send_telegram_text(chat_id, "Бот активен. Для привязки откройте личный кабинет и нажмите «Подключить Telegram».", reply_to_message_id)
            except Exception as exc:
                print(f"[telegram] error sending start message: {exc}")
        return

    if not telegram_user_id:
        return

    link = await find_user_by_telegram_id(pool, telegram_user_id)
    if not link:
        try:
            await send_telegram_text(chat_id, "Этот Telegram ещё не привязан. Откройте личный кабинет и подключите Telegram.", reply_to_message_id)
        except Exception as exc:
            print(f"[telegram] error sending unlinked message: {exc}")
        return

    await update_telegram_presence(pool, telegram_user_id, message)
    if not text:
        try:
            await send_telegram_text(chat_id, "Пока обрабатываю только текстовые сообщения.", reply_to_message_id)
        except Exception as exc:
            print(f"[telegram] error sending non-text message: {exc}")
        return

    session_key = f"telegram:{telegram_user_id}:chat:{chat_id}"
    try:
        response_text = await route_telegram_message_to_instance(
            pool,
            int(link["user_id"]),
            text,
            session_key,
            loading_chat_id=chat_id,
            loading_reply_to_message_id=reply_to_message_id,
)
    except Exception as exc:  # noqa: BLE001
        try:
            await send_telegram_text(chat_id, f"Ошибка при обращении к контейнеру: {exc}", reply_to_message_id)
        except Exception:
            pass
        return

    try:
        await send_telegram_text(chat_id, response_text, reply_to_message_id)
    except Exception as exc:
        print(f"[telegram] error sending response to user {telegram_user_id}: {exc}")


@app.post("/telegram/webhook/setup")
async def telegram_webhook_setup(admin=Depends(get_admin_user)):
    try:
        return await set_telegram_webhook()
    except TelegramGatewayConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/telegram/webhook/info")
async def telegram_webhook_info(admin=Depends(get_admin_user)):
    try:
        return await get_telegram_webhook_info()
    except TelegramGatewayConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/metrics/{user_id}")
async def get_metrics(user_id: int, request: Request, admin=Depends(get_admin_user)):
    pool = request.app.state.pool
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
