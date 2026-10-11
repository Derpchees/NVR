"""Live video: stream setup, WebRTC (WHEP) relay, HLS proxy and diagnostics."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .. import config, db, mediamtx, onvif_service
from ..schemas import PlayerEventInput

router = APIRouter(prefix="/api/cameras/{camera_id}")

PLAYER_EVENTS: dict[str, deque[dict[str, object]]] = {}
SAFE_ASSET = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
SAFE_SESSION = set("0123456789abcdef-")


@router.post("/stream")
async def start_stream(camera_id: str):
    """Point MediaMTX at the camera's best profile and return player URLs."""
    camera = db.get_camera(camera_id)
    password = db.camera_password(camera)
    try:
        profile_token, uri = await asyncio.to_thread(
            onvif_service.best_stream, camera["host"], camera["port"], camera["username"], password
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo solicitar la URL RTSP a la cámara") from exc
    try:
        await mediamtx.ensure_path(
            camera_id, onvif_service.with_credentials(uri, camera["username"], password)
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="MediaMTX no está disponible") from exc
    if profile_token != camera["profile_token"]:
        await asyncio.to_thread(_save_profile, camera_id, profile_token)
        onvif_service.forget(camera_id)
    return {
        "playlist": f"/api/cameras/{camera_id}/stream/index.m3u8",
        "whep": f"/api/cameras/{camera_id}/whep",
    }


def _save_profile(camera_id: str, profile_token: str) -> None:
    with db.connect() as connection:
        connection.execute(
            "UPDATE cameras SET profile_token = ? WHERE id = ?", (profile_token, camera_id)
        )


@router.post("/whep")
async def whep_offer(camera_id: str, request: Request):
    """Relay a WebRTC (WHEP) offer to MediaMTX, which only listens on loopback.

    Signaling goes through the authenticated panel; media then flows over the
    ICE candidates MediaMTX negotiates for this single session.
    """
    db.get_camera(camera_id)
    offer = await request.body()
    if not offer or len(offer) > config.WHEP_OFFER_LIMIT or not offer.startswith(b"v=0"):
        raise HTTPException(status_code=400, detail="Oferta WebRTC no válida")
    try:
        upstream = await mediamtx.whep_offer(camera_id, offer)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="WebRTC no está disponible") from exc
    if upstream.status_code != 201:
        try:
            reason = upstream.json().get("error")
        except (ValueError, AttributeError):
            reason = None
        raise HTTPException(status_code=502, detail=reason or "WebRTC rechazó la sesión")
    session = upstream.headers.get("location", "").rstrip("/").rsplit("/", 1)[-1]
    headers = {"Cache-Control": "no-store"}
    if session and set(session) <= SAFE_SESSION:
        headers["Location"] = f"/api/cameras/{camera_id}/whep/{session}"
    return Response(content=upstream.content, status_code=201, media_type="application/sdp", headers=headers)


@router.delete("/whep/{session}", status_code=204)
async def whep_close(camera_id: str, session: str):
    db.get_camera(camera_id)
    if not session or not set(session) <= SAFE_SESSION:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    await mediamtx.whep_close(camera_id, session)
    return Response(status_code=204)


@router.get("/stream/{asset}")
async def stream_asset(camera_id: str, asset: str):
    db.get_camera(camera_id)
    if not asset or not set(asset) <= SAFE_ASSET:
        raise HTTPException(status_code=404, detail="Recurso no encontrado")
    try:
        upstream = await mediamtx.hls_asset(camera_id, asset)
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=502,
            detail="La cámara no entregó vídeo RTSP. Comprueba que RTSP está activo y que la cuenta tiene permisos de streaming.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="El servidor de vídeo no está disponible") from exc
    if upstream.status_code >= 400:
        try:
            reason = upstream.json().get("error")
        except (ValueError, AttributeError):
            reason = None
        if reason and "timed out" in reason:
            detail = "No hay vídeo RTSP. Revisa las credenciales y que esta cuenta tenga permisos de streaming en la cámara."
        else:
            detail = f"RTSP no disponible: {reason}" if reason else "El flujo aún no está disponible"
        raise HTTPException(status_code=502, detail=detail)
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/playback-events", status_code=204)
def record_playback_event(camera_id: str, event: PlayerEventInput):
    """Keep a short, credential-free window of browser playback observations."""
    db.get_camera(camera_id)
    events = PLAYER_EVENTS.setdefault(camera_id, deque(maxlen=config.PLAYER_EVENT_LIMIT))
    events.append(
        {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "event": event.event,
            "detail": event.detail,
            "fatal": event.fatal,
            "media_time": event.media_time,
            "latency": event.latency,
        }
    )
    return Response(status_code=204)


@router.get("/diagnostics")
async def camera_diagnostics(camera_id: str):
    """Live-safe observations for delay/freeze investigations.

    Source URLs are never included: they contain camera credentials.
    """
    camera = db.get_camera(camera_id)
    path_state, playlist = await asyncio.gather(
        mediamtx.path_state(camera_id), mediamtx.playlist_state(camera_id)
    )
    try:
        profile = await asyncio.to_thread(onvif_service.profile_summary, camera)
    except Exception:
        profile = {"available": False, "error": "No se pudo consultar el perfil ONVIF"}
    return {
        "profile": profile,
        "mediamtx": path_state,
        "hls": playlist,
        "browser_events": list(PLAYER_EVENTS.get(camera_id, ())),
    }
