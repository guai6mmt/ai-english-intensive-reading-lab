from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from .config import PROJECT_ROOT, config
from .database import connect, transaction, utc_now


router = APIRouter()
password_hasher = PasswordHasher()
_LOGIN_LOCK = threading.Lock()
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
STATIC_DIR = PROJECT_ROOT / "static"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_PATHS = {
    "/login",
    "/service-worker.js",
    "/manifest.webmanifest",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/health/live",
    "/health/ready",
}
PUBLIC_API_PATHS = {
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
}
SESSION_TOUCH_SECONDS = 300


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=10, max_length=256)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _users_exist() -> bool:
    with connect() as connection:
        return bool(connection.execute("SELECT 1 FROM users LIMIT 1").fetchone())


def _session_for_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    now = utc_now()
    with connect() as connection:
        row = connection.execute(
            """SELECT sessions.id AS session_id, sessions.csrf_token,
                      sessions.expires_at, sessions.last_seen_at,
                      users.id, users.username, users.role
               FROM sessions
               JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
            (_token_hash(token), now),
        ).fetchone()
        if not row:
            return None
        try:
            last_seen = datetime.fromisoformat(row["last_seen_at"])
        except (TypeError, ValueError):
            last_seen = datetime.min.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_seen).total_seconds() >= SESSION_TOUCH_SECONDS:
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (now, row["session_id"]),
            )
            connection.commit()
        return dict(row)


def _create_session(user_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(40)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=config.session_days)
    with transaction() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(timespec="seconds"),))
        connection.execute(
            """INSERT INTO sessions
               (id, user_id, token_hash, csrf_token, expires_at, created_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex,
                user_id,
                _token_hash(token),
                csrf_token,
                expires.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
            ),
        )
    return token, csrf_token


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.session_cookie,
        value=token,
        max_age=config.session_days * 86400,
        httponly=True,
        secure=config.cookie_secure,
        samesite="strict",
        path="/",
    )


class SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path

        # Static assets and public infrastructure endpoints never need a DB lookup.
        if path.startswith("/static/") or path in PUBLIC_PATHS:
            request.state.user = None
            return await call_next(request)

        # WebDAV owns its HTTP Basic authentication and never uses browser cookies.
        if path == "/dav" or path.startswith("/dav/"):
            request.state.user = None
            return await call_next(request)

        token = request.cookies.get(config.session_cookie)
        session = _session_for_token(token)
        request.state.user = session

        if path in PUBLIC_API_PATHS:
            return await call_next(request)

        if not session:
            if path.startswith(("/api/", "/audio/", "/covers/")):
                return JSONResponse({"detail": "请先登录。"}, status_code=401)
            return RedirectResponse(url="/login", status_code=303)

        if path.startswith("/api/"):
            if request.method in UNSAFE_METHODS:
                supplied = request.headers.get("x-csrf-token", "")
                if not supplied or not secrets.compare_digest(supplied, session["csrf_token"]):
                    return JSONResponse({"detail": "安全令牌无效，请刷新页面后重试。"}, status_code=403)

        return await call_next(request)


@router.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


@router.get("/media", include_in_schema=False)
def media_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "media.html")


@router.get("/service-worker.js", include_in_schema=False)
def service_worker() -> FileResponse:
    response = FileResponse(STATIC_DIR / "service-worker.js", media_type="text/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@router.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@router.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    user = request.state.user
    return {
        "setup_required": not _users_exist(),
        "authenticated": bool(user),
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]} if user else None,
        "csrf_token": user["csrf_token"] if user else None,
    }


@router.post("/api/auth/setup")
def setup_admin(credentials: Credentials, response: Response) -> dict[str, Any]:
    username = credentials.username.strip()
    if not username:
        raise HTTPException(400, "用户名不能为空。")
    user_id = uuid.uuid4().hex
    now = utc_now()
    try:
        with transaction() as connection:
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise HTTPException(409, "管理员已经创建，请直接登录。")
            connection.execute(
                "INSERT INTO users(id, username, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
                (user_id, username, password_hasher.hash(credentials.password), now),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "用户名已经存在。") from exc
    token, csrf_token = _create_session(user_id)
    _set_session_cookie(response, token)
    return {"ok": True, "user": {"id": user_id, "username": username, "role": "admin"}, "csrf_token": csrf_token}


@router.post("/api/auth/login")
def login(credentials: Credentials, response: Response, request: Request) -> dict[str, Any]:
    client_key = request.client.host if request.client else "unknown"
    now_ts = time.time()
    with _LOGIN_LOCK:
        recent = [stamp for stamp in _LOGIN_ATTEMPTS.get(client_key, []) if now_ts - stamp < 600]
        _LOGIN_ATTEMPTS[client_key] = recent
        if len(recent) >= 8:
            raise HTTPException(429, "登录尝试过多，请十分钟后再试。")
    with connect() as connection:
        row = connection.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ? COLLATE NOCASE",
            (credentials.username.strip(),),
        ).fetchone()
    if not row:
        with _LOGIN_LOCK:
            _LOGIN_ATTEMPTS.setdefault(client_key, []).append(now_ts)
        raise HTTPException(401, "用户名或密码错误。")
    try:
        password_hasher.verify(row["password_hash"], credentials.password)
    except (VerifyMismatchError, InvalidHashError):
        with _LOGIN_LOCK:
            _LOGIN_ATTEMPTS.setdefault(client_key, []).append(now_ts)
        raise HTTPException(401, "用户名或密码错误。") from None
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.pop(client_key, None)
    with connect() as connection:
        connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utc_now(), row["id"]))
        connection.commit()
    token, csrf_token = _create_session(row["id"])
    _set_session_cookie(response, token)
    return {
        "ok": True,
        "user": {"id": row["id"], "username": row["username"], "role": row["role"]},
        "csrf_token": csrf_token,
    }


@router.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    user = request.state.user
    return {
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
        "csrf_token": user["csrf_token"],
    }


@router.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get(config.session_cookie)
    if token:
        with connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
            connection.commit()
    response.delete_cookie(config.session_cookie, path="/")
    return {"ok": True}


def current_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "请先登录。")
    return user
