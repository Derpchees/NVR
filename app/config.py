"""Paths, environment settings and fixed limits shared by every module."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "cameras.sqlite3"
KEY_PATH = DATA_DIR / "master.key"

MEDIA_API = os.environ.get("MEDIAMTX_API", "http://127.0.0.1:9997")
MEDIA_HLS = os.environ.get("MEDIAMTX_HLS", "http://127.0.0.1:8888")
MEDIA_HLS_SECRET = os.environ.get("MEDIAMTX_HLS_SECRET", "")
MEDIA_WEBRTC = os.environ.get("MEDIAMTX_WEBRTC", "http://127.0.0.1:8889")
MEDIA_PLAYBACK = os.environ.get("MEDIAMTX_PLAYBACK", "http://127.0.0.1:9996")

RECORDINGS_DIR = Path(os.environ.get("RECORDINGS_DIR", "/recordings"))
RECORDINGS_MAX_BYTES = int(float(os.environ.get("RECORDINGS_MAX_GB", "500")) * 1024**3)
RETENTION_INTERVAL_SECONDS = 300
PATH_CHECK_SECONDS = 60
STREAM_URI_REFRESH_SECONDS = 1800
MOTION_EVENT_MAX_DAYS = 180

WHEP_OFFER_LIMIT = 64_000
MAX_DISCOVERY_SECONDS = 4
PLAYER_EVENT_LIMIT = 50

SESSION_COOKIE = "nvr_session"
SESSION_SECONDS = 7 * 24 * 3600
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 60
PUBLIC_PATHS = {"/healthz", "/login", "/api/auth/login"}
