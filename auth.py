"""
Авторизация: регистрация, логин, JWT сессии.
Подключается к main.py через app.include_router(auth_router).
"""

import os
import secrets as _secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

SECRET_KEY = os.environ.get("JWT_SECRET", "change-me-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.environ.get("ADMIN_EMAILS", "").split(",")
    if email.strip()
}
ADMIN_USER_IDS = {
    int(uid.strip())
    for uid in os.environ.get("ADMIN_USER_IDS", "").split(",")
    if uid.strip().isdigit()
}

auth_router = APIRouter(prefix="/auth", tags=["auth"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def normalize_email_or_400(value: Any) -> str:
    """Проверяет email и возвращает понятную 400-ошибку вместо FastAPI 422."""
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="Введите email")

    try:
        checked = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise HTTPException(
            status_code=400,
            detail="Введите корректный email, например user@example.com",
        ) from exc

    return checked.normalized.lower()


def require_password_or_400(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=400, detail="Введите пароль")
    return value


def require_token_or_400(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="Некорректная ссылка сброса пароля")
    return value.strip()


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


async def get_current_user(request: Request) -> dict:
    """Dependency — извлекает пользователя из Bearer токена."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(auth[7:])
    return {"user_id": int(payload["sub"]), "email": payload["email"]}


async def get_admin_user(user=Depends(get_current_user)) -> dict:
    is_admin = (
        user["email"].lower() in ADMIN_EMAILS
        or user["user_id"] in ADMIN_USER_IDS
    )
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ─── Schemas ──────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: Any = ""
    password: Any = ""


class LoginRequest(BaseModel):
    email: Any = ""
    password: Any = ""


# ─── Endpoints ────────────────────────────────────────────────────────────────

@auth_router.post("/register")
async def register(req: RegisterRequest, request: Request):
    pool = request.app.state.pool
    email = normalize_email_or_400(req.email)
    password = require_password_or_400(req.password)

    existing = await pool.fetchrow(
        "SELECT id FROM users WHERE email = $1", email
    )
    if existing:
        raise HTTPException(status_code=409, detail="Email уже зарегистрирован")

    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен быть не короче 8 символов")

    hashed = hash_password(password)

    user_id = await pool.fetchval(
        """
        INSERT INTO users (email, password_hash)
        VALUES ($1, $2)
        RETURNING id
        """,
        email,
        hashed,
    )

    token = create_token(user_id, email)
    return {"token": token, "user_id": user_id, "email": email}


@auth_router.post("/login")
async def login(req: LoginRequest, request: Request):
    pool = request.app.state.pool
    email = normalize_email_or_400(req.email)
    password = require_password_or_400(req.password)

    row = await pool.fetchrow(
        "SELECT id, password_hash FROM users WHERE email = $1", email
    )
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    token = create_token(row["id"], email)
    return {"token": token, "user_id": row["id"], "email": email}


@auth_router.get("/me")
async def me(user=Depends(get_current_user)):
    return user


# ─── Password reset ───────────────────────────────────────────────────────────

APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")


class ForgotRequest(BaseModel):
    email: Any = ""


class ResetRequest(BaseModel):
    token: Any = ""
    new_password: Any = ""


@auth_router.post("/password/forgot")
async def password_forgot(req: ForgotRequest, request: Request):
    """Send a password-reset email. Always returns 200 to avoid leaking whether
    an email is registered."""
    from brevo import BrevoConfigError, send_password_reset_email

    pool = request.app.state.pool
    email = normalize_email_or_400(req.email)

    row = await pool.fetchrow("SELECT id FROM users WHERE email = $1", email)
    if not row:
        # Silently succeed — don't reveal whether email exists
        return {"ok": True}

    # Invalidate any previous unused tokens for this user
    await pool.execute(
        "UPDATE password_reset_tokens SET used_at = now() WHERE user_id = $1 AND used_at IS NULL",
        row["id"],
    )

    token = _secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await pool.execute(
        """
        INSERT INTO password_reset_tokens (token, user_id, expires_at)
        VALUES ($1, $2, $3)
        """,
        token,
        row["id"],
        expires_at,
    )

    base = APP_BASE_URL or str(request.base_url).rstrip("/")
    reset_url = f"{base}/?reset_token={token}"

    try:
        await send_password_reset_email(email, reset_url)
    except BrevoConfigError as exc:
        raise HTTPException(status_code=500, detail=f"Mail service not configured: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to send email: {exc}") from exc

    return {"ok": True}


@auth_router.post("/password/reset")
async def password_reset(req: ResetRequest, request: Request):
    pool = request.app.state.pool
    reset_token = require_token_or_400(req.token)
    new_password = require_password_or_400(req.new_password)

    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен быть не короче 8 символов")

    row = await pool.fetchrow(
        """
        UPDATE password_reset_tokens
        SET used_at = now()
        WHERE token = $1
          AND used_at IS NULL
          AND expires_at > now()
        RETURNING user_id
        """,
        reset_token,
    )
    if not row:
        raise HTTPException(status_code=400, detail="Ссылка сброса пароля недействительна или устарела")

    hashed = hash_password(new_password)
    user_row = await pool.fetchrow(
        "UPDATE users SET password_hash = $1 WHERE id = $2 RETURNING email",
        hashed,
        row["user_id"],
    )

    token = create_token(row["user_id"], user_row["email"])
    return {"ok": True, "token": token, "email": user_row["email"]}
