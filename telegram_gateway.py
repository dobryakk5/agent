
import asyncio
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from docker_manager import get_container_ip
from instance_service import sync_instance_to_admin_settings

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
TELEGRAM_BOT_USERNAME = (os.getenv("TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
TELEGRAM_WEBHOOK_SECRET = (os.getenv("TELEGRAM_WEBHOOK_SECRET", "") or "").strip()
TELEGRAM_WEBHOOK_URL = (os.getenv("TELEGRAM_WEBHOOK_URL", "") or "").strip()
TELEGRAM_API_BASE = (os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org") or "https://api.telegram.org").rstrip("/")
OPENCLAW_AGENT_ID = (os.getenv("OPENCLAW_AGENT_ID", "main") or "main").strip()


class TelegramGatewayConfigError(RuntimeError):
    pass


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


async def wait_for_instance_http(user_id: int, gateway_token: str, timeout_seconds: int = 45) -> str:
    deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
    loop = asyncio.get_running_loop()
    last_error = None
    while datetime.now(timezone.utc).timestamp() < deadline:
        try:
            ip = await loop.run_in_executor(None, lambda: get_container_ip(user_id))
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"http://{ip}:18789/v1/models",
                    headers={"Authorization": f"Bearer {gateway_token}"},
                )
                if r.status_code == 200:
                    return ip
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        await asyncio.sleep(1)
    raise RuntimeError(f"Gateway did not become ready for user {user_id}: {last_error}")


async def route_telegram_message_to_instance(pool, user_id: int, text: str, session_key: str) -> str:
    row = await pool.fetchrow(
        """
        SELECT user_id, status, gateway_token
        FROM user_instances
        WHERE user_id = $1
        """,
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")

    gateway_token = (row["gateway_token"] or "").strip()
    if not gateway_token:
        raise RuntimeError("Instance gateway token is missing")

    if row["status"] != "running":
        await sync_instance_to_admin_settings(pool, user_id, force_status="running")

    ip = await wait_for_instance_http(user_id, gateway_token)

    headers = {
        "Authorization": f"Bearer {gateway_token}",
        "Content-Type": "application/json",
        "x-openclaw-agent-id": OPENCLAW_AGENT_ID,
        "x-openclaw-message-channel": "telegram",
        "x-openclaw-session-key": session_key,
    }
    body = {
        "model": "openclaw",
        "input": text,
        "user": session_key,
    }

    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(f"http://{ip}:18789/v1/responses", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    return extract_output_text(data) or "Не удалось извлечь текст ответа от ассистента."


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
