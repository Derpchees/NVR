"""Panel account: password hashing, login sessions and the auth middleware."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from . import config, db

LOGIN_FAILURES: dict[str, tuple[int, float]] = {}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_valid(token: str | None) -> bool:
    if not token:
        return False
    with db.connect() as connection:
        row = connection.execute(
            "SELECT expires_at FROM sessions WHERE token_hash = ?", (_token_hash(token),)
        ).fetchone()
    return bool(row and row["expires_at"] > time.time())


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO sessions (token_hash, expires_at) VALUES (?, ?)",
            (_token_hash(token), time.time() + config.SESSION_SECONDS),
        )
    return token


def end_session(token: str) -> None:
    with db.connect() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        config.SESSION_COOKIE,
        token,
        max_age=config.SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        path="/",
    )


def login_locked_for(client: str) -> int:
    """Seconds left on a brute-force lock for this client, or 0."""
    _, locked_until = LOGIN_FAILURES.get(client, (0, 0.0))
    return max(0, int(locked_until - time.time()) + 1) if locked_until > time.time() else 0


def record_login(client: str, success: bool) -> None:
    if success:
        LOGIN_FAILURES.pop(client, None)
        return
    failures = LOGIN_FAILURES.get(client, (0, 0.0))[0] + 1
    LOGIN_FAILURES[client] = (
        (0, time.time() + config.LOGIN_LOCK_SECONDS)
        if failures >= config.LOGIN_MAX_FAILURES
        else (failures, 0.0)
    )


async def require_session(request: Request, call_next):
    path = request.url.path
    if path in config.PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)
    # The cookie is SameSite=Strict; also refuse cross-origin writes explicitly.
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origin:
        if urlsplit(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "Origen no permitido"}, status_code=403)
    if not await asyncio.to_thread(session_valid, request.cookies.get(config.SESSION_COOKIE)):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Sesión caducada"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)
