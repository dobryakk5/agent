import asyncio
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from docker_manager import ensure_container_started
from instance_service import sync_instance_to_admin_settings
from runtime_state import record_instance_runtime_state, refresh_instance_runtime_state

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
TELEGRAM_BOT_USERNAME = (os.getenv("TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
TELEGRAM_WEBHOOK_SECRET = (os.getenv("TELEGRAM_WEBHOOK_SECRET", "") or "").strip()
TELEGRAM_WEBHOOK_URL = (os.getenv("TELEGRAM_WEBHOOK_URL", "") or "").strip()
TELEGRAM_API_BASE = (os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org") or "https://api.telegram.org").rstrip("/")
OPENCLAW_AGENT_ID = (os.getenv("OPENCLAW_AGENT_ID", "main") or "main").strip()

AGENT_LOADING_TEXT = "Загружаю агента…"


class TelegramGatewayConfigError(RuntimeError):
    pass


_INSTANCE_LOCKS: dict[int, asyncio.Lock] = {}
_INSTANCE_LOCKS_GUARD = asyncio.Lock()


async def get_instance_lock(user_id: int) -> asyncio.Lock:
    async with _INSTANCE_LOCKS_GUARD:
        lock = _INSTANCE_LOCKS.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _INSTANCE_LOCKS[user_id] = lock
        return lock


def ensure_telegram_bot_config() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise TelegramGatewayConfigError("TELEGRAM_BOT_TOKEN is not configured")


def ensure_telegram_link_config() -> None:
    ensure_telegram_bot_config()
    if not TELEGRAM_BOT_USERNAME:
        raise TelegramGatewayConfigError("TELEGRAM_BOT_USERNAME is not configured")


def verify_telegram_secret(header_value: str | None) -> bool:
    if not TELEGRAM_WEBHOOK_SECRET:
        return True
    return hmac.compare_digest(header_value or "", TELEGRAM_WEBHOOK_SECRET)


async def create_telegram_link_token(pool, user_id: int) -> dict[str, Any]:
    ensure_telegram_link_config()
    token = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=20)
    await pool.execute(
        """
        INSERT INTO telegram_link_tokens (token, user_id, expires_at)
        VALUES ($1, $2, $3)
        """,
        token,
        user_id,
        expires_at,
    )
    return {
        "token": token,
        "expires_at": expires_at.isoformat(),
        "deep_link_url": build_telegram_deep_link(token),
        "bot_username": TELEGRAM_BOT_USERNAME,
    }


async def consume_telegram_link_token(pool, token: str) -> int:
    row = await pool.fetchrow(
        """
        UPDATE telegram_link_tokens
        SET used_at = now()
        WHERE token = $1
          AND used_at IS NULL
          AND expires_at > now()
        RETURNING user_id
        """,
        token,
    )
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired Telegram link token")
    return int(row["user_id"])


async def upsert_telegram_link(pool, user_id: int, message: dict[str, Any]) -> None:
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    telegram_user_id = int(sender.get("id") or 0)

    await pool.execute(
        "DELETE FROM telegram_links WHERE telegram_user_id = $1 AND user_id <> $2",
        telegram_user_id,
        user_id,
    )
    await pool.execute(
        """
        INSERT INTO telegram_links (
            user_id, telegram_user_id, telegram_chat_id,
            telegram_username, telegram_first_name, telegram_last_name,
            linked_at, last_seen_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, now(), now())
        ON CONFLICT (user_id) DO UPDATE
        SET telegram_user_id = EXCLUDED.telegram_user_id,
            telegram_chat_id = EXCLUDED.telegram_chat_id,
            telegram_username = EXCLUDED.telegram_username,
            telegram_first_name = EXCLUDED.telegram_first_name,
            telegram_last_name = EXCLUDED.telegram_last_name,
            last_seen_at = now()
        """,
        user_id,
        telegram_user_id,
        int(chat.get("id") or 0),
        sender.get("username"),
        sender.get("first_name"),
        sender.get("last_name"),
    )


async def unlink_telegram_account(pool, user_id: int) -> None:
    await pool.execute("DELETE FROM telegram_links WHERE user_id = $1", user_id)


async def find_user_by_telegram_id(pool, telegram_user_id: int):
    return await pool.fetchrow(
        """
        SELECT l.user_id, l.telegram_chat_id, i.status, i.gateway_token
        FROM telegram_links l
        JOIN user_instances i ON i.user_id = l.user_id
        WHERE l.telegram_user_id = $1
        """,
        telegram_user_id,
    )


async def update_telegram_presence(pool, telegram_user_id: int, message: dict[str, Any]) -> None:
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    await pool.execute(
        """
        UPDATE telegram_links
        SET telegram_chat_id = $2,
            telegram_username = $3,
            telegram_first_name = $4,
            telegram_last_name = $5,
            last_seen_at = now()
        WHERE telegram_user_id = $1
        """,
        telegram_user_id,
        int(chat.get("id") or 0),
        sender.get("username"),
        sender.get("first_name"),
        sender.get("last_name"),
    )


async def send_telegram_text(chat_id: int, text: str, reply_to_message_id: int | None = None) -> dict[str, Any]:
    ensure_telegram_bot_config()
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4000] if text else "Пустой ответ от ассистента.",
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
        )
        r.raise_for_status()
        return r.json()


async def send_agent_loading_message_if_possible(
    chat_id: int | None,
    reply_to_message_id: int | None = None,
) -> None:
    if not chat_id:
        return

    try:
        await send_telegram_text(chat_id, AGENT_LOADING_TEXT, reply_to_message_id)
    except Exception as exc:  # noqa: BLE001
        # Не валим основной запрос из-за ошибки отправки промежуточного сообщения.
        print(f"[telegram] could not send loading message: {exc}")


async def set_telegram_webhook() -> dict[str, Any]:
    ensure_telegram_bot_config()
    if not TELEGRAM_WEBHOOK_URL:
        raise TelegramGatewayConfigError("TELEGRAM_WEBHOOK_URL is not configured")

    payload: dict[str, Any] = {"url": TELEGRAM_WEBHOOK_URL}
    if TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = TELEGRAM_WEBHOOK_SECRET

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
            json=payload,
        )
        r.raise_for_status()
        return r.json()


async def get_telegram_webhook_info() -> dict[str, Any]:
    ensure_telegram_bot_config()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo")
        r.raise_for_status()
        return r.json()



TELEGRAM_QUEUE_POLL_INTERVAL_SECONDS = float(os.getenv("TELEGRAM_QUEUE_POLL_INTERVAL_SECONDS", "2"))
TELEGRAM_QUEUE_BATCH_SIZE = int(os.getenv("TELEGRAM_QUEUE_BATCH_SIZE", "5"))
TELEGRAM_UPDATE_MAX_ATTEMPTS = int(os.getenv("TELEGRAM_UPDATE_MAX_ATTEMPTS", "5"))
GATEWAY_READY_TIMEOUT_SECONDS = int(os.getenv("GATEWAY_READY_TIMEOUT_SECONDS", "180"))
GATEWAY_SLOW_NOTICE_SECONDS = int(os.getenv("GATEWAY_SLOW_NOTICE_SECONDS", "60"))

AGENT_STARTING_TEXT = "Запускаю агента…"
AGENT_STILL_LOADING_TEXT = "Агент ещё загружается. Сообщение сохранено, я продолжу попытку автоматически."
AGENT_UNAVAILABLE_TEXT = "Сервис агентов временно недоступен. Сообщение сохранено, попробую обработать его автоматически."
AGENT_ADMIN_CHECK_TEXT = "Агент пока не запускается. Сообщение сохранено, но нужна проверка администратора."

_NOTIFY_FLAGS = {
    "agent_started_message_sent",
    "agent_slow_message_sent",
    "agent_unavailable_message_sent",
    "admin_check_message_sent",
}


class RetryTelegramUpdate(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retry_delay_seconds: int = 60,
        user_message: str | None = None,
        notify_flag: str | None = None,
    ):
        super().__init__(message)
        self.retry_delay_seconds = retry_delay_seconds
        self.user_message = user_message
        self.notify_flag = notify_flag


class PermanentTelegramUpdateError(RuntimeError):
    pass


def _extract_update_metadata(update: dict[str, Any]) -> dict[str, Any]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}

    update_id = update.get("update_id")
    try:
        update_id = int(update_id)
    except Exception:
        update_id = None

    chat_id = chat.get("id")
    try:
        chat_id = int(chat_id) if chat_id is not None else None
    except Exception:
        chat_id = None

    telegram_user_id = sender.get("id")
    try:
        telegram_user_id = int(telegram_user_id) if telegram_user_id is not None else None
    except Exception:
        telegram_user_id = None

    message_id = message.get("message_id")
    try:
        message_id = int(message_id) if message_id is not None else None
    except Exception:
        message_id = None

    text = (message.get("text") or "").strip()

    if not message:
        initial_status = "ignored"
    elif chat.get("type") != "private":
        initial_status = "ignored"
    else:
        initial_status = "pending"

    return {
        "telegram_update_id": update_id,
        "telegram_user_id": telegram_user_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "initial_status": initial_status,
    }


async def save_telegram_update(pool, update: dict[str, Any]) -> dict[str, Any]:
    """Persist an incoming Telegram update before any slow Docker/OpenClaw work.

    For already-linked Telegram users we resolve user_id immediately, so the
    queue row is visibly owned by the target account before the worker starts.
    /start link_... updates may legitimately have user_id = NULL until the
    worker consumes the link token.
    """
    meta = _extract_update_metadata(update)
    if meta["telegram_update_id"] is None:
        # Telegram normally always provides update_id. Do not raise to Telegram;
        # just keep the webhook fast and observable in logs.
        return {"ok": False, "stored": False, "reason": "missing update_id"}

    resolved_user_id = None
    if meta["telegram_user_id"] is not None:
        link = await pool.fetchrow(
            "SELECT user_id FROM telegram_links WHERE telegram_user_id = $1",
            meta["telegram_user_id"],
        )
        if link:
            resolved_user_id = int(link["user_id"])

    row = await pool.fetchrow(
        """
        INSERT INTO telegram_updates (
            telegram_update_id, telegram_user_id, chat_id, message_id,
            text, payload_json, status, user_id
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
        ON CONFLICT (telegram_update_id) DO UPDATE
        SET user_id = COALESCE(telegram_updates.user_id, EXCLUDED.user_id),
            telegram_user_id = COALESCE(telegram_updates.telegram_user_id, EXCLUDED.telegram_user_id),
            chat_id = COALESCE(telegram_updates.chat_id, EXCLUDED.chat_id),
            message_id = COALESCE(telegram_updates.message_id, EXCLUDED.message_id),
            updated_at = now()
        RETURNING id, status, user_id, (xmax = 0) AS inserted
        """,
        meta["telegram_update_id"],
        meta["telegram_user_id"],
        meta["chat_id"],
        meta["message_id"],
        meta["text"],
        json.dumps(update, ensure_ascii=False),
        meta["initial_status"],
        resolved_user_id,
    )
    return {
        "ok": True,
        "stored": bool(row and row["inserted"]),
        "id": int(row["id"]) if row else None,
        "status": row["status"] if row else "duplicate",
        "user_id": int(row["user_id"]) if row and row["user_id"] is not None else None,
    }


async def telegram_update_worker_loop(pool) -> None:
    """Poll PostgreSQL queue and deliver Telegram updates to OpenClaw agents."""
    while True:
        try:
            await process_telegram_update_queue(pool, limit=TELEGRAM_QUEUE_BATCH_SIZE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # If the migration has not been applied yet, do not kill the app.
            print(f"[telegram-worker] queue loop error: {exc}")
        await asyncio.sleep(TELEGRAM_QUEUE_POLL_INTERVAL_SECONDS)


async def process_telegram_update_queue(pool, limit: int = 5) -> int:
    rows = await pool.fetch(
        """
        WITH picked AS (
            SELECT id
            FROM telegram_updates
            WHERE (
                    status IN ('pending', 'retry')
                    AND next_attempt_at <= now()
                )
                OR (
                    status = 'locked'
                    AND locked_at < now() - interval '5 minutes'
                )
                OR (
                    status IN (
                        'checking_agent', 'starting_agent', 'waiting_gateway',
                        'sending_to_agent', 'waiting_agent_response',
                        'sending_to_telegram'
                    )
                    AND updated_at < now() - interval '15 minutes'
                )
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT $1
        )
        UPDATE telegram_updates u
        SET status = 'locked',
            locked_at = now(),
            updated_at = now()
        FROM picked
        WHERE u.id = picked.id
        RETURNING u.*
        """,
        limit,
    )

    for row in rows:
        try:
            await _handle_telegram_update_row(pool, dict(row))
        except RetryTelegramUpdate as exc:
            await _schedule_retry(pool, dict(row), exc)
        except PermanentTelegramUpdateError as exc:
            await _fail_update(pool, dict(row), str(exc))
        except Exception as exc:  # noqa: BLE001
            await _schedule_retry(
                pool,
                dict(row),
                RetryTelegramUpdate(str(exc), retry_delay_seconds=60),
            )

    return len(rows)


async def _set_update_status(pool, update_id: int, status: str, **fields: Any) -> None:
    allowed = {
        "user_id",
        "instance_id",
        "attempts",
        "next_attempt_at",
        "locked_at",
        "agent_started_message_sent",
        "agent_slow_message_sent",
        "agent_unavailable_message_sent",
        "admin_check_message_sent",
        "sent_to_agent_at",
        "agent_response_at",
        "telegram_response_at",
        "agent_response_text",
        "last_error",
    }
    assignments = ["status = $2", "updated_at = now()"]
    values: list[Any] = [update_id, status]
    idx = 3
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Unsupported telegram_updates field: {key}")
        assignments.append(f"{key} = ${idx}")
        values.append(value)
        idx += 1

    await pool.execute(
        f"""
        UPDATE telegram_updates
        SET {', '.join(assignments)}
        WHERE id = $1
        """,
        *values,
    )


async def _notify_once(
    pool,
    row: dict[str, Any],
    text: str,
    flag: str,
) -> None:
    if flag not in _NOTIFY_FLAGS:
        raise ValueError(f"Unsupported notify flag: {flag}")

    chat_id = row.get("chat_id")
    if not chat_id:
        return

    current = await pool.fetchrow(
        f"SELECT {flag} FROM telegram_updates WHERE id = $1",
        row["id"],
    )
    if current and current[flag]:
        return

    try:
        await send_telegram_text(int(chat_id), text, row.get("message_id"))
    except Exception as exc:  # noqa: BLE001
        # Status notifications must not break the main delivery pipeline.
        print(f"[telegram-worker] could not send status message for update {row['id']}: {exc}")
        return

    await pool.execute(
        f"UPDATE telegram_updates SET {flag} = true, updated_at = now() WHERE id = $1",
        row["id"],
    )
    row[flag] = True


async def _schedule_retry(pool, row: dict[str, Any], exc: RetryTelegramUpdate) -> None:
    if exc.user_message and exc.notify_flag:
        await _notify_once(pool, row, exc.user_message, exc.notify_flag)

    updated = await pool.fetchrow(
        """
        UPDATE telegram_updates
        SET status = CASE
                WHEN attempts + 1 >= $2 THEN 'failed'
                ELSE 'retry'
            END,
            attempts = attempts + 1,
            next_attempt_at = CASE
                WHEN attempts + 1 >= $2 THEN next_attempt_at
                ELSE now() + ($3::text || ' seconds')::interval
            END,
            last_error = $4,
            updated_at = now()
        WHERE id = $1
        RETURNING attempts, status
        """,
        row["id"],
        TELEGRAM_UPDATE_MAX_ATTEMPTS,
        int(exc.retry_delay_seconds),
        str(exc),
    )

    if updated and updated["status"] == "failed":
        await _notify_once(pool, row, AGENT_ADMIN_CHECK_TEXT, "admin_check_message_sent")


async def _fail_update(pool, row: dict[str, Any], error: str) -> None:
    await _set_update_status(pool, int(row["id"]), "failed", last_error=error)
    if row.get("chat_id"):
        await _notify_once(pool, row, AGENT_ADMIN_CHECK_TEXT, "admin_check_message_sent")


async def _record_runtime_state(
    pool,
    user_id: int,
    state: dict[str, Any],
    *,
    gateway_state: str = "not_checked",
    gateway_ready: bool = False,
    last_error: str | None = None,
) -> None:
    await record_instance_runtime_state(
        pool,
        user_id,
        state,
        gateway_state=gateway_state,
        gateway_ready=gateway_ready,
        last_error=last_error,
    )


async def _inspect_container(pool, user_id: int, gateway_state: str = "not_checked") -> dict[str, Any]:
    return await refresh_instance_runtime_state(pool, user_id, gateway_state=gateway_state)


async def _ensure_container_ready_for_gateway(pool, row: dict[str, Any], user_id: int) -> dict[str, Any]:
    await _set_update_status(pool, int(row["id"]), "checking_agent", user_id=user_id)

    state = await _inspect_container(pool, user_id)

    if not state.get("docker_available", True):
        raise RetryTelegramUpdate(
            "Docker daemon is unavailable",
            retry_delay_seconds=60,
            user_message=AGENT_UNAVAILABLE_TEXT,
            notify_flag="agent_unavailable_message_sent",
        )

    if not state.get("exists"):
        await _notify_once(pool, row, AGENT_STARTING_TEXT, "agent_started_message_sent")
        await _set_update_status(pool, int(row["id"]), "starting_agent", user_id=user_id)
        try:
            await sync_instance_to_admin_settings(pool, user_id, force_status="running")
        except Exception as exc:  # noqa: BLE001
            raise RetryTelegramUpdate(
                f"Could not create/recreate container: {exc}",
                retry_delay_seconds=60,
                user_message=AGENT_UNAVAILABLE_TEXT,
                notify_flag="agent_unavailable_message_sent",
            ) from exc
        state = await _inspect_container(pool, user_id)

    if state.get("status") in {"created", "exited", "dead"}:
        await _notify_once(pool, row, AGENT_STARTING_TEXT, "agent_started_message_sent")
        await _set_update_status(pool, int(row["id"]), "starting_agent", user_id=user_id)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: ensure_container_started(user_id))
        except Exception as exc:  # noqa: BLE001
            raise RetryTelegramUpdate(
                f"Could not start container: {exc}",
                retry_delay_seconds=60,
                user_message=AGENT_UNAVAILABLE_TEXT,
                notify_flag="agent_unavailable_message_sent",
            ) from exc
        state = await _inspect_container(pool, user_id)

    if state.get("status") == "restarting":
        await _notify_once(pool, row, "Агент перезапускается. Сообщение сохранено, я попробую отправить его после запуска.", "agent_started_message_sent")
        raise RetryTelegramUpdate("Container is restarting", retry_delay_seconds=30)

    if not state.get("running"):
        raise RetryTelegramUpdate(
            f"Container is not running: {state.get('status')}",
            retry_delay_seconds=30,
            user_message=AGENT_UNAVAILABLE_TEXT,
            notify_flag="agent_unavailable_message_sent",
        )

    await pool.execute(
        "UPDATE user_instances SET status='running', stopped_at=NULL WHERE user_id=$1",
        user_id,
    )

    if not state.get("has_ip"):
        raise RetryTelegramUpdate("Container has no IP address", retry_delay_seconds=15)

    return state


async def _wait_for_gateway_ready(
    pool,
    row: dict[str, Any],
    user_id: int,
    gateway_token: str,
    timeout_seconds: int = GATEWAY_READY_TIMEOUT_SECONDS,
) -> str:
    await _set_update_status(pool, int(row["id"]), "waiting_gateway", user_id=user_id)
    deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
    started_at = datetime.now(timezone.utc).timestamp()
    last_error: str | None = None

    while datetime.now(timezone.utc).timestamp() < deadline:
        try:
            state = await _ensure_container_ready_for_gateway(pool, row, user_id)
            ip = state.get("ip")
            if not ip:
                raise RuntimeError("Container has no IP address")

            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"http://{ip}:18789/v1/models",
                    headers={"Authorization": f"Bearer {gateway_token}"},
                )

            if r.status_code == 200:
                await _record_runtime_state(pool, user_id, state, gateway_state="ready", gateway_ready=True)
                return str(ip)

            if r.status_code in {401, 403}:
                await _record_runtime_state(
                    pool,
                    user_id,
                    state,
                    gateway_state="unauthorized",
                    gateway_ready=False,
                    last_error=f"Gateway returned HTTP {r.status_code}",
                )
                raise PermanentTelegramUpdateError("Gateway authorization failed")

            last_error = f"Gateway returned HTTP {r.status_code}"
            await _record_runtime_state(
                pool,
                user_id,
                state,
                gateway_state="error",
                gateway_ready=False,
                last_error=last_error,
            )
        except PermanentTelegramUpdateError:
            raise
        except RetryTelegramUpdate as exc:
            last_error = str(exc)
            # Let short docker/network states retry through the DB queue instead of
            # blocking this worker for the full gateway timeout.
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            try:
                state = await _inspect_container(pool, user_id, gateway_state="starting")
            except Exception:
                state = {}
            if state:
                await _record_runtime_state(
                    pool,
                    user_id,
                    state,
                    gateway_state="starting",
                    gateway_ready=False,
                    last_error=last_error,
                )

        if datetime.now(timezone.utc).timestamp() - started_at >= GATEWAY_SLOW_NOTICE_SECONDS:
            await _notify_once(pool, row, AGENT_STILL_LOADING_TEXT, "agent_slow_message_sent")

        await asyncio.sleep(2)

    raise RetryTelegramUpdate(
        f"Gateway did not become ready for user {user_id}: {last_error}",
        retry_delay_seconds=60,
    )


async def _deliver_text_to_agent(
    pool,
    row: dict[str, Any],
    user_id: int,
    text: str,
    session_key: str,
) -> str:
    lock = await get_instance_lock(user_id)

    async with lock:
        instance = await pool.fetchrow(
            """
            SELECT id, user_id, gateway_token
            FROM user_instances
            WHERE user_id = $1
            """,
            user_id,
        )
        if not instance:
            raise PermanentTelegramUpdateError("Instance not found")

        gateway_token = (instance["gateway_token"] or "").strip()
        if not gateway_token:
            raise PermanentTelegramUpdateError("Instance gateway token is missing")

        await _set_update_status(
            pool,
            int(row["id"]),
            "checking_agent",
            user_id=user_id,
            instance_id=instance["id"],
        )

        ip = await _wait_for_gateway_ready(pool, row, user_id, gateway_token)

        await _set_update_status(
            pool,
            int(row["id"]),
            "sending_to_agent",
            user_id=user_id,
            instance_id=instance["id"],
            sent_to_agent_at=datetime.now(timezone.utc),
        )

        headers = {
            "Authorization": f"Bearer {gateway_token}",
            "Content-Type": "application/json",
            "x-openclaw-agent-id": OPENCLAW_AGENT_ID,
            "x-openclaw-message-channel": "telegram",
            "x-openclaw-session-key": session_key,
            "x-openclaw-message-id": f"telegram_update:{row['telegram_update_id']}",
        }
        body = {
            "model": "openclaw",
            "input": text,
            "user": session_key,
        }

        await _set_update_status(pool, int(row["id"]), "waiting_agent_response")
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(
                f"http://{ip}:18789/v1/responses",
                headers=headers,
                json=body,
            )
            r.raise_for_status()
            data = r.json()

        response_text = extract_output_text(data) or "Не удалось извлечь текст ответа от ассистента."
        await _set_update_status(
            pool,
            int(row["id"]),
            "sending_to_telegram",
            agent_response_text=response_text,
            agent_response_at=datetime.now(timezone.utc),
        )
        return response_text


async def _send_final_telegram_response(pool, row: dict[str, Any], response_text: str) -> None:
    await _set_update_status(pool, int(row["id"]), "sending_to_telegram")
    await send_telegram_text(int(row["chat_id"]), response_text, row.get("message_id"))
    await _set_update_status(
        pool,
        int(row["id"]),
        "done",
        telegram_response_at=datetime.now(timezone.utc),
        last_error=None,
    )


async def _handle_telegram_update_row(pool, row: dict[str, Any]) -> None:
    update = row.get("payload_json") or {}
    if isinstance(update, str):
        update = json.loads(update)

    message = update.get("message") or update.get("edited_message")
    if not message:
        await _set_update_status(pool, int(row["id"]), "ignored")
        return

    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text = (message.get("text") or "").strip()
    chat_id = int(chat.get("id") or row.get("chat_id") or 0)
    telegram_user_id = int(sender.get("id") or row.get("telegram_user_id") or 0)
    reply_to_message_id = message.get("message_id") or row.get("message_id")

    if chat.get("type") != "private":
        await _set_update_status(pool, int(row["id"]), "ignored")
        return

    if row.get("agent_response_text") and not row.get("telegram_response_at"):
        await _send_final_telegram_response(pool, row, str(row["agent_response_text"]))
        return

    if text.startswith("/start"):
        arg = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        if arg.startswith("link_"):
            token = arg[5:]
            try:
                user_id = await consume_telegram_link_token(pool, token)
                await upsert_telegram_link(pool, user_id, message)
                await _set_update_status(pool, int(row["id"]), "sending_to_telegram", user_id=user_id)
                await send_telegram_text(chat_id, "Telegram успешно привязан к вашему ассистенту.", reply_to_message_id)
                await _set_update_status(pool, int(row["id"]), "done", telegram_response_at=datetime.now(timezone.utc))
            except HTTPException:
                await send_telegram_text(chat_id, "Ссылка привязки недействительна или уже использована.", reply_to_message_id)
                await _set_update_status(pool, int(row["id"]), "rejected", telegram_response_at=datetime.now(timezone.utc))
        else:
            await send_telegram_text(chat_id, "Бот активен. Для привязки откройте личный кабинет и нажмите «Подключить Telegram».", reply_to_message_id)
            await _set_update_status(pool, int(row["id"]), "done", telegram_response_at=datetime.now(timezone.utc))
        return

    if not telegram_user_id:
        await _set_update_status(pool, int(row["id"]), "rejected", last_error="Telegram user id is missing")
        return

    link = await find_user_by_telegram_id(pool, telegram_user_id)
    if not link:
        await send_telegram_text(chat_id, "Этот Telegram ещё не привязан. Откройте личный кабинет и подключите Telegram.", reply_to_message_id)
        await _set_update_status(pool, int(row["id"]), "rejected", telegram_response_at=datetime.now(timezone.utc))
        return

    user_id = int(link["user_id"])
    await _set_update_status(pool, int(row["id"]), "checking_agent", user_id=user_id)
    await update_telegram_presence(pool, telegram_user_id, message)

    if not text:
        await send_telegram_text(chat_id, "Пока обрабатываю только текстовые сообщения.", reply_to_message_id)
        await _set_update_status(pool, int(row["id"]), "rejected", telegram_response_at=datetime.now(timezone.utc))
        return

    session_key = f"telegram:{telegram_user_id}:chat:{chat_id}"
    response_text = await _deliver_text_to_agent(pool, row, user_id, text, session_key)
    await _send_final_telegram_response(pool, row, response_text)


async def ensure_instance_started_for_telegram(
    pool,
    user_id: int,
    loading_chat_id: int | None = None,
    loading_reply_to_message_id: int | None = None,
) -> None:
    """Backward-compatible helper for older callers.

    New Telegram delivery goes through the persistent queue above.
    """
    fake_row = {
        "id": 0,
        "chat_id": loading_chat_id,
        "message_id": loading_reply_to_message_id,
        "agent_started_message_sent": False,
        "agent_slow_message_sent": False,
        "agent_unavailable_message_sent": False,
        "admin_check_message_sent": False,
    }
    await _ensure_container_ready_for_gateway(pool, fake_row, user_id)


async def wait_for_instance_http(user_id: int, gateway_token: str, timeout_seconds: int = 180) -> str:
    """Backward-compatible gateway wait without DB state tracking."""
    deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
    loop = asyncio.get_running_loop()
    last_error = None

    while datetime.now(timezone.utc).timestamp() < deadline:
        try:
            state = await loop.run_in_executor(None, lambda: ensure_container_started(user_id))
            ip = state.get("ip")
            if not ip:
                raise RuntimeError("Container has no IP address")

            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"http://{ip}:18789/v1/models",
                    headers={"Authorization": f"Bearer {gateway_token}"},
                )

                if r.status_code == 200:
                    return str(ip)

                last_error = RuntimeError(f"Gateway returned HTTP {r.status_code}")

        except Exception as exc:  # noqa: BLE001
            last_error = exc

        await asyncio.sleep(1)

    raise RuntimeError(f"Gateway did not become ready for user {user_id}: {last_error}")


async def route_telegram_message_to_instance(
    pool,
    user_id: int,
    text: str,
    session_key: str,
    loading_chat_id: int | None = None,
    loading_reply_to_message_id: int | None = None,
) -> str:
    """Backward-compatible direct route. Prefer the DB queue for webhooks."""
    row = {
        "id": 0,
        "telegram_update_id": 0,
        "chat_id": loading_chat_id,
        "message_id": loading_reply_to_message_id,
        "agent_started_message_sent": False,
        "agent_slow_message_sent": False,
        "agent_unavailable_message_sent": False,
        "admin_check_message_sent": False,
    }
    return await _deliver_text_to_agent(pool, row, user_id, text, session_key)


def extract_output_text(data: dict[str, Any]) -> str:
    text = (data.get("output_text") or "").strip()
    if text:
        return text

    parts: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and content.get("text"):
                    parts.append(str(content["text"]).strip())
        elif item.get("type") == "output_text" and item.get("text"):
            parts.append(str(item["text"]).strip())
    return "\n\n".join(p for p in parts if p)


def build_telegram_deep_link(token: str) -> str:
    ensure_telegram_link_config()
    return f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=link_{quote(token)}"
