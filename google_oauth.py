
import json
import os
from pathlib import Path
from urllib.parse import urlencode

import httpx

GOOGLE_OAUTH_JSON_PATH = os.environ.get("GOOGLE_OAUTH_JSON_PATH", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")
GOOGLE_SCOPES = os.environ.get(
    "GOOGLE_SCOPES",
    " ".join(
        [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/drive.metadata.readonly",
            "https://www.googleapis.com/auth/documents.readonly",
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/tasks",
            "https://www.googleapis.com/auth/contacts.readonly",
        ]
    ),
)


class GoogleOAuthConfigError(RuntimeError):
    pass


def _load_google_client_config() -> dict:
    if not GOOGLE_OAUTH_JSON_PATH:
        raise GoogleOAuthConfigError("GOOGLE_OAUTH_JSON_PATH is not set")

    path = Path(GOOGLE_OAUTH_JSON_PATH)
    if not path.exists():
        raise GoogleOAuthConfigError(f"Google OAuth file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    client = data.get("web") or data.get("installed")
    if not client:
        raise GoogleOAuthConfigError("OAuth JSON must contain either 'web' or 'installed'")

    redirect_uri = GOOGLE_REDIRECT_URI.strip()
    if not redirect_uri:
        redirect_uris = client.get("redirect_uris") or []
        if not redirect_uris:
            raise GoogleOAuthConfigError("No redirect URI found in OAuth config")
        redirect_uri = redirect_uris[0]

    return {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": redirect_uri,
    }


def build_auth_url(state: str) -> str:
    cfg = _load_google_client_config()
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


async def get_google_userinfo(access_token: str) -> dict:
    """Fetch the authenticated user's profile from Google.

    Returns a dict with at minimum: sub (google_id), email, email_verified.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def exchange_code_for_tokens(code: str) -> dict:
    cfg = _load_google_client_config()

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": cfg["redirect_uri"],
            },
        )
        response.raise_for_status()
        return response.json()
