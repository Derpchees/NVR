"""Camera inventory: list, discover, test, add and remove."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import db, mediamtx, onvif_service, recorder
from ..schemas import CameraInput
from .streaming import PLAYER_EVENTS

router = APIRouter(prefix="/api/cameras")


@router.get("")
def list_cameras():
    with db.connect() as connection:
        cameras = connection.execute(
            "SELECT id, name, host, port, username FROM cameras ORDER BY created_at, name"
        ).fetchall()
    return [dict(camera) for camera in cameras]


@router.post("/discover")
def discover_cameras():
    try:
        return onvif_service.discover()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="No se pudo enviar el anuncio ONVIF multicast") from exc


@router.post("/test")
def test_camera(camera: CameraInput):
    try:
        profiles = onvif_service.profiles(camera.host, camera.port, camera.username, camera.password)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="No se pudo consultar ONVIF; verifica IP, puerto y credenciales",
        ) from exc
    return {"best": profiles[0], "profiles": len(profiles)}


@router.post("", status_code=201)
def add_camera(camera: CameraInput):
    try:
        profile_token, _ = onvif_service.best_stream(
            camera.host, camera.port, camera.username, camera.password
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="No se pudo validar ONVIF y obtener el perfil RTSP"
        ) from exc
    row = {
        "id": str(uuid.uuid4()),
        "name": camera.name,
        "host": camera.host,
        "port": camera.port,
        "username": camera.username,
        "password_enc": db.cipher().encrypt(camera.password.encode()).decode(),
        "profile_token": profile_token,
    }
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO cameras (id, name, host, port, username, password_enc, profile_token) "
            "VALUES (:id, :name, :host, :port, :username, :password_enc, :profile_token)",
            row,
        )
    recorder.wake()
    return {key: row[key] for key in ("id", "name", "host", "port", "username")}


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: str):
    db.get_camera(camera_id)
    await mediamtx.remove_path(camera_id)
    await asyncio.to_thread(_delete_row, camera_id)
    PLAYER_EVENTS.pop(camera_id, None)
    onvif_service.forget(camera_id)
    recorder.wake()
    return Response(status_code=204)


def _delete_row(camera_id: str) -> None:
    with db.connect() as connection:
        connection.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
