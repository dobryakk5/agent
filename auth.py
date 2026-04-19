"""
Авторизация: регистрация, логин, JWT сессии.
Подключается к main.py через app.include_router(auth_router).
"""

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

SECRET_KEY = os.environ.get("JWT_SECRET", "change-me-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

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


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(request: Request) -> dict:
    """Dependency — извлекает пользователя из Bearer токена."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(auth[7:])
    return {"user_id": int(payload["sub"]), "email": payload["email"]}


# ─── Schemas ──────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@auth_router.post("/register")
async def register(req: RegisterRequest, request: Request):
    pool = request.app.state.pool

    existing = await pool.fetchrow(
        "SELECT id FROM users WHERE email = $1", req.email
    )
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    hashed = hash_password(req.password)

    user_id = await pool.fetchval(
        """
        INSERT INTO users (email, password_hash)
        VALUES ($1, $2)
        RETURNING id
        """,
        req.email,
        hashed,
    )

    token = create_token(user_id, req.email)
    return {"token": token, "user_id": user_id, "email": req.email}


@auth_router.post("/login")
async def login(req: LoginRequest, request: Request):
    pool = request.app.state.pool

    row = await pool.fetchrow(
        "SELECT id, password_hash FROM users WHERE email = $1", req.email
    )
    if not row or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(row["id"], req.email)
    return {"token": token, "user_id": row["id"], "email": req.email}


@auth_router.get("/me")
async def me(user=Depends(get_current_user)):
    return user
