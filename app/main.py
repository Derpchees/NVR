"""NVR CCTV panel: application assembly.

Modules: config (settings), db (storage), security (login/sessions),
onvif_service (cameras), mediamtx (video server) and routers/* (HTTP API).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, db, recorder, security
from .routers import auth, cameras, controls, pages, recordings, streaming


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not config.MEDIA_HLS_SECRET:
        raise RuntimeError("MEDIAMTX_HLS_SECRET must be set before starting the CCTV panel")
    db.init()
    tasks = recorder.start()
    yield
    await recorder.stop(tasks)


app = FastAPI(title="NVR CCTV", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.middleware("http")(security.require_session)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
for router in (pages.router, auth.router, cameras.router, controls.router, streaming.router, recordings.router):
    app.include_router(router)
