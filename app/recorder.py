"""Background services: keep every camera recording, enforce the storage
budget and log ONVIF motion events.

MediaMTX keeps path configuration in memory only, so a supervisor re-creates
missing paths (e.g. after a MediaMTX restart) instead of relying on viewers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from . import config, db, mediamtx, onvif_service

log = logging.getLogger("nvr.recorder")

_uri_refreshed: dict[str, float] = {}
_motion_tasks: dict[str, asyncio.Task] = {}
_wake = asyncio.Event()


def wake() -> None:
    """Ask the supervisor to reconcile now (camera added or removed)."""
    _wake.set()


def _cameras() -> list[dict[str, object]]:
    with db.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM cameras").fetchall()]


async def _ensure_recording(camera: dict[str, object]) -> None:
    camera_id = str(camera["id"])
    fresh = time.monotonic() - _uri_refreshed.get(camera_id, -1e9) < config.STREAM_URI_REFRESH_SECONDS
    if fresh and await mediamtx.path_configured(camera_id):
        return
    password = db.camera_password(camera)
    profile_token, uri = await asyncio.to_thread(
        onvif_service.best_stream, camera["host"], camera["port"], camera["username"], password
    )
    await mediamtx.ensure_path(camera_id, onvif_service.with_credentials(uri, camera["username"], password))
    if profile_token != camera["profile_token"]:
        with db.connect() as connection:
            connection.execute("UPDATE cameras SET profile_token = ? WHERE id = ?", (profile_token, camera_id))
        onvif_service.forget(camera_id)
    _uri_refreshed[camera_id] = time.monotonic()


async def supervise() -> None:
    while True:
        cameras = await asyncio.to_thread(_cameras)
        current = {str(camera["id"]) for camera in cameras}
        for camera in cameras:
            try:
                await _ensure_recording(camera)
            except Exception as exc:  # camera offline: retry next round
                log.warning("No se pudo asegurar la grabación de %s: %s", camera["name"], exc)
            camera_id = str(camera["id"])
            if camera_id not in _motion_tasks or _motion_tasks[camera_id].done():
                _motion_tasks[camera_id] = asyncio.create_task(_watch_motion(camera))
        for camera_id in set(_motion_tasks) - current:
            _motion_tasks.pop(camera_id).cancel()
            _uri_refreshed.pop(camera_id, None)
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=config.PATH_CHECK_SECONDS)
        except asyncio.TimeoutError:
            pass


# ---- Storage budget ----

def _segments() -> list[tuple[float, int, Path]]:
    found = []
    for directory, _, files in os.walk(config.RECORDINGS_DIR):
        for name in files:
            path = Path(directory, name)
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            found.append((stat.st_mtime, stat.st_size, path))
    return found


def usage() -> dict[str, int]:
    segments = _segments()
    disk = os.statvfs(config.RECORDINGS_DIR)
    return {
        "used_bytes": sum(size for _, size, _ in segments),
        "budget_bytes": config.RECORDINGS_MAX_BYTES,
        "disk_free_bytes": disk.f_bavail * disk.f_frsize,
        "segments": len(segments),
    }


def prune() -> int:
    """Delete the oldest segments until the recordings fit the budget."""
    segments = sorted(_segments())
    total = sum(size for _, size, _ in segments)
    newest_per_camera = {}
    for mtime, _, path in segments:
        newest_per_camera[path.parent] = path
    removed = 0
    for _, size, path in segments:
        if total <= config.RECORDINGS_MAX_BYTES:
            break
        if newest_per_camera.get(path.parent) == path:
            continue  # still being written
        try:
            path.unlink()
            total -= size
            removed += 1
        except FileNotFoundError:
            pass
    for directory in config.RECORDINGS_DIR.iterdir():
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return removed


def _prune_motion_events() -> None:
    with db.connect() as connection:
        connection.execute(
            "DELETE FROM motion_events WHERE started_at < ?",
            (time.time() - config.MOTION_EVENT_MAX_DAYS * 86400,),
        )


async def enforce_budget() -> None:
    while True:
        try:
            removed = await asyncio.to_thread(prune)
            if removed:
                log.info("Grabaciones: %d segmentos antiguos borrados por el límite de espacio", removed)
            await asyncio.to_thread(_prune_motion_events)
        except Exception as exc:
            log.warning("No se pudo aplicar el límite de grabaciones: %s", exc)
        await asyncio.sleep(config.RETENTION_INTERVAL_SECONDS)


# ---- Motion events ----

def _is_motion(message, topic: str) -> bool | None:
    # IsMotion is specific to motion detectors; the generic State item is also
    # used by relays and inputs, so it only counts under a motion topic.
    names = ("IsMotion", "State") if "motion" in topic.lower() else ("IsMotion",)
    try:
        for part in message.Message._value_1:
            for item in part.iter():
                if item.get("Name") in names:
                    return item.get("Value", "").lower() == "true"
    except Exception:
        return None
    return None


def _record_motion(camera_id: str, active: bool) -> None:
    now = time.time()
    with db.connect() as connection:
        open_event = connection.execute(
            "SELECT id FROM motion_events WHERE camera_id = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (camera_id,),
        ).fetchone()
        if active and not open_event:
            connection.execute(
                "INSERT INTO motion_events (camera_id, started_at) VALUES (?, ?)", (camera_id, now)
            )
        elif not active and open_event:
            connection.execute("UPDATE motion_events SET ended_at = ? WHERE id = ?", (now, open_event["id"]))


def _close_open_events(camera_id: str) -> None:
    with db.connect() as connection:
        connection.execute(
            "UPDATE motion_events SET ended_at = ? WHERE camera_id = ? AND ended_at IS NULL",
            (time.time(), camera_id),
        )


async def _watch_motion(camera: dict[str, object]) -> None:
    """Pull ONVIF motion notifications; reconnect with backoff on errors."""
    camera_id = str(camera["id"])
    delay = 5
    while True:
        try:
            device = await asyncio.to_thread(
                onvif_service.connect,
                camera["host"], camera["port"], camera["username"], db.camera_password(camera),
            )
            events = device.create_events_service()
            await asyncio.to_thread(events.CreatePullPointSubscription)
            pull = device.create_pullpoint_service()
            delay = 5
            while True:
                result = await asyncio.to_thread(pull.PullMessages, {"Timeout": "PT10S", "MessageLimit": 50})
                for message in result.NotificationMessage or []:
                    topic = str(getattr(message.Topic, "_value_1", "") or "")
                    active = _is_motion(message, topic)
                    if active is not None:
                        await asyncio.to_thread(_record_motion, camera_id, active)
        except asyncio.CancelledError:
            await asyncio.to_thread(_close_open_events, camera_id)
            raise
        except Exception as exc:
            log.info("Eventos de movimiento de %s no disponibles (%s); reintento en %ds", camera["name"], exc, delay)
            await asyncio.to_thread(_close_open_events, camera_id)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


def motion_between(camera_id: str, start: float, end: float) -> list[dict[str, float | None]]:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT started_at, ended_at FROM motion_events WHERE camera_id = ? "
            "AND started_at < ? AND (ended_at IS NULL OR ended_at > ?) ORDER BY started_at",
            (camera_id, end, start),
        ).fetchall()
    return [{"start": row["started_at"], "end": row["ended_at"]} for row in rows]


def start() -> list[asyncio.Task]:
    config.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return [asyncio.create_task(supervise()), asyncio.create_task(enforce_budget())]


async def stop(tasks: list[asyncio.Task]) -> None:
    for task in [*tasks, *_motion_tasks.values()]:
        task.cancel()
    await asyncio.gather(*tasks, *_motion_tasks.values(), return_exceptions=True)
    _motion_tasks.clear()
