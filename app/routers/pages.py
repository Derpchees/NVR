"""HTML pages and health check."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

from .. import config, security

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/")
async def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@router.get("/login")
async def login_page(request: Request):
    if await asyncio.to_thread(security.session_valid, request.cookies.get(config.SESSION_COOKIE)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(config.STATIC_DIR / "login.html")
