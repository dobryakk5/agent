"""
Yandex OAuth 2.0 helper.
Используется для подключения Yandex 360 (Диск, Почта, Календарь, Telemost)
к пользовательскому инстансу агента.
"""

import os
import urllib.parse

import httpx

YANDEX_CLIENT_ID     = os.environ.get("YANDEX_CLIENT_ID", "")
YANDEX_CLIENT_SECRET = os.environ.get("YANDEX_CLIENT_SECRET", "")
YANDEX_REDIRECT_URI  = os.environ.get("YANDEX_REDIRECT_URI", "")

# Минимальный набор scopes для yax
YANDEX_SCOPES = " ".join([
    "cloud_api:disk.app_folder",
    "cloud_api:disk.info",
    "cloud_api:disk.read",
    "cloud_api:disk.write",
    "calendar:all",
    "mail:imap_full",
    "mail:smtp",
    "telemost-api:conferences.create",
    "login:email",  # нужен для /info (getYandexLogin в yax.js)
])


class YandexOAuthConfigError(Exception):
    pass


def build_yandex_auth_url(state: str) -> str:
    if not YANDEX_CLIENT_ID or not YANDEX_REDIRECT_URI:
        raise YandexOAuthConfigError(
            "YANDEX_CLIENT_ID и YANDEX_REDIRECT_URI должны быть заданы в .env"
        )
    params = {
        "response_type": "code",
        "client_id":     YANDEX_CLIENT_ID,
        "redirect_uri":  YANDEX_REDIRECT_URI,
        "scope":         YANDEX_SCOPES,
        "state":         state,
        "force_confirm": "yes",  # показывать окно выдачи прав каждый раз
    }
    return "https://oauth.yandex.ru/authorize?" + urllib.parse.urlencode(params)


async def exchange_yandex_code(code: str) -> dict:
    """Обменивает authorization code на access_token + refresh_token."""
    if not YANDEX_CLIENT_ID:
        raise YandexOAuthConfigError("YANDEX_CLIENT_ID не задан в .env")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://oauth.yandex.ru/token",
            data={
                "grant_type":    "authorization_code",
                "code":          code,
                "client_id":     YANDEX_CLIENT_ID,
                "client_secret": YANDEX_CLIENT_SECRET,
                "redirect_uri":  YANDEX_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise ValueError(f"Yandex token error {resp.status_code}: {resp.text[:200]}")
    token = resp.json()
    if "access_token" not in token:
        raise ValueError(f"Yandex вернул неожиданный ответ: {token}")
    return token
