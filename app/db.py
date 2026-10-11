"""SQLite storage and encryption of camera credentials."""

from __future__ import annotations

import os
import sqlite3
import time

from cryptography.fernet import Fernet
from fastapi import HTTPException

from . import config, security


def cipher() -> Fernet:
    if not config.KEY_PATH.exists():
        key = Fernet.generate_key()
        try:
            descriptor = os.open(config.KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as key_file:
                key_file.write(key)
        except FileExistsError:
            pass
    return Fernet(config.KEY_PATH.read_bytes())


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(config.DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cipher()
    with connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS cameras (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                username TEXT NOT NULL,
                password_enc TEXT NOT NULL,
                profile_token TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                expires_at REAL NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS motion_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS motion_events_camera_time ON motion_events (camera_id, started_at)"
        )
        if connection.execute("SELECT 1 FROM users").fetchone() is None:
            # First boot: seed the single panel account from .env; afterwards
            # it is managed from the panel and .env is no longer consulted.
            password = os.environ.get("APP_PASSWORD", "")
            if not password:
                raise RuntimeError("APP_PASSWORD must be set to create the first panel account")
            connection.execute(
                "INSERT INTO users (id, username, password_hash) VALUES (1, ?, ?)",
                (os.environ.get("APP_USER", "admin"), security.hash_password(password)),
            )
        connection.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))


def get_camera(camera_id: str) -> sqlite3.Row:
    with connect() as connection:
        camera = connection.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    if camera is None:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    return camera


def camera_password(camera: sqlite3.Row) -> str:
    try:
        return cipher().decrypt(camera["password_enc"].encode()).decode()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="No se pudo descifrar la credencial") from exc
