"""Recorded video: day timeline, playback/download proxy and storage status."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import db, mediamtx, recorder

router = APIRouter(prefix="/api")

MAX_RANGE_SECONDS = 2 * 86400
MAX_CLIP_SECONDS = 4 * 3600


def _parse(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Fecha no válida") from exc
    if moment.tzinfo is None:
        raise HTTPException(status_code=422, detail="La fecha debe incluir zona horaria")
    return moment.astimezone(timezone.utc)


def _rfc3339(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@router.get("/recordings/status")
async def recordings_status():
    usage = await asyncio.to_thread(recorder.usage)
    return usage


@router.get("/cameras/{camera_id}/recordings")
async def recording_timeline(camera_id: str, start: str = Query(...), end: str = Query(...)):
    """Recorded spans and motion events between two instants (e.g. one local day)."""
    db.get_camera(camera_id)
    begin, finish = _parse(start), _parse(end)
    if not 0 < (finish - begin).total_seconds() <= MAX_RANGE_SECONDS:
        raise HTTPException(status_code=422, detail="Rango de tiempo no válido")
    try:
        spans = await mediamtx.recording_list(camera_id, _rfc3339(begin), _rfc3339(finish))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="El servidor de grabaciones no responde") from exc
    motion = await asyncio.to_thread(recorder.motion_between, camera_id, begin.timestamp(), finish.timestamp())
    return {
        "spans": [{"start": span["start"], "duration": float(span["duration"])} for span in spans],
        "motion": motion,
    }


@router.get("/cameras/{camera_id}/recordings/video")
async def recording_video(
    camera_id: str,
    start: str = Query(...),
    duration: float = Query(..., gt=0, le=MAX_CLIP_SECONDS),
    download: bool = False,
):
    """Stream recorded video from MediaMTX: fragmented MP4 to watch, MP4 to save."""
    db.get_camera(camera_id)
    begin = _parse(start)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
    request = mediamtx.recording_request(
        camera_id, _rfc3339(begin), duration, "mp4" if download else "fmp4"
    )
    try:
        upstream = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=503, detail="El servidor de grabaciones no responde") from exc
    if upstream.status_code != 200:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=404, detail="No hay grabación en ese momento")

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    headers = {"Cache-Control": "no-store"}
    if download:
        # No filename: the page names the file in the viewer's local time.
        headers["Content-Disposition"] = "attachment"
    return StreamingResponse(body(), media_type="video/mp4", headers=headers)
