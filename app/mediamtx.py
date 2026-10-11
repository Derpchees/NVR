"""Client for the loopback-only MediaMTX API, HLS and WebRTC endpoints."""

from __future__ import annotations

import httpx

from . import config

HLS_HEADERS = {"Authorization": f"Bearer {config.MEDIA_HLS_SECRET}"}


def _path_config(source: str) -> dict[str, object]:
    # Always pulled and recorded, so recordings do not depend on viewers and
    # live view starts instantly.
    return {"source": source, "sourceOnDemand": False, "record": True, "rtspTransport": "tcp"}


async def path_configured(camera_id: str) -> bool:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(f"{config.MEDIA_API}/v3/config/paths/get/{camera_id}")
    return response.status_code == 200


async def ensure_path(camera_id: str, source: str) -> None:
    """Create or update the camera's path, leaving an unchanged one alone.

    Replacing a path restarts its RTSP pull, drops everyone watching it and
    starts a new recording segment, so an identical configuration is kept.
    """
    wanted = _path_config(source)
    async with httpx.AsyncClient(timeout=5) as client:
        current = await client.get(f"{config.MEDIA_API}/v3/config/paths/get/{camera_id}")
        if current.status_code == 200:
            existing = current.json()
            if all(existing.get(key) == value for key, value in wanted.items()):
                return
        action = "replace" if current.status_code == 200 else "add"
        result = await client.post(
            f"{config.MEDIA_API}/v3/config/paths/{action}/{camera_id}", json=wanted
        )
        result.raise_for_status()


async def remove_path(camera_id: str) -> None:
    async with httpx.AsyncClient(timeout=3) as client:
        try:
            await client.delete(f"{config.MEDIA_API}/v3/config/paths/delete/{camera_id}")
        except httpx.HTTPError:
            pass


async def path_state(camera_id: str) -> dict[str, object]:
    # MediaMTX may wait up to sourceOnDemandStartTimeout (8 s) for RTSP.
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(f"{config.MEDIA_API}/v3/paths/get/{camera_id}")
        except httpx.HTTPError:
            return {"available": False, "error": "MediaMTX no respondió"}
    if response.status_code != 200:
        return {"available": False, "status_code": response.status_code}
    payload = response.json()
    readers = payload.get("readers", [])
    return {
        "available": True,
        "ready": payload.get("ready"),
        "online": payload.get("online"),
        "readers": len(readers),
        "webrtc_readers": sum(reader.get("type") == "webRTCSession" for reader in readers),
        # Codec actually received over RTSP; ONVIF metadata can disagree.
        "track_details": [
            {"codec": track.get("codec"), **(track.get("codecProps") or {})}
            for track in payload.get("tracks2", [])
        ],
        "inbound_frames_in_error": payload.get("inboundFramesInError"),
    }


def _playlist_summary(playlist: str) -> dict[str, object]:
    lines = [line.strip() for line in playlist.splitlines() if line.startswith(("#EXT-X-", "#EXTINF:"))]
    return {
        "part_inf": next((line for line in lines if line.startswith("#EXT-X-PART-INF:")), None),
        "server_control": next(
            (line for line in lines if line.startswith("#EXT-X-SERVER-CONTROL:")), None
        ),
        "part_count": sum(line.startswith("#EXT-X-PART:") for line in lines),
        "segment_count": sum(line.startswith("#EXTINF:") for line in lines),
    }


async def playlist_state(camera_id: str) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(
                f"{config.MEDIA_HLS}/{camera_id}/index.m3u8", headers=HLS_HEADERS
            )
            # index.m3u8 is a multivariant playlist; parts and segments are
            # only listed in the media playlist it points to.
            variant = next(
                (line.strip() for line in response.text.splitlines() if line.strip() and not line.startswith("#")),
                None,
            ) if response.status_code == 200 else None
            if variant:
                response = await client.get(
                    f"{config.MEDIA_HLS}/{camera_id}/{variant}", headers=HLS_HEADERS
                )
        except httpx.HTTPError:
            return {"available": False, "error": "Playlist HLS no disponible"}
    if response.status_code != 200:
        return {"available": False, "status_code": response.status_code}
    return {"available": True, **_playlist_summary(response.text)}


async def hls_asset(camera_id: str, asset: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15) as client:
        return await client.get(f"{config.MEDIA_HLS}/{camera_id}/{asset}", headers=HLS_HEADERS)


async def whep_offer(camera_id: str, offer: bytes) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15) as client:
        return await client.post(
            f"{config.MEDIA_WEBRTC}/{camera_id}/whep",
            content=offer,
            headers={"Content-Type": "application/sdp"},
        )


async def recording_list(camera_id: str, start: str, end: str) -> list[dict[str, object]]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{config.MEDIA_PLAYBACK}/list", params={"path": camera_id, "start": start, "end": end}
        )
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return response.json()


def recording_request(camera_id: str, start: str, duration: float, file_format: str) -> httpx.Request:
    return httpx.Request(
        "GET",
        f"{config.MEDIA_PLAYBACK}/get",
        params={"path": camera_id, "start": start, "duration": f"{duration:.3f}", "format": file_format},
    )


async def whep_close(camera_id: str, session: str) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            await client.delete(f"{config.MEDIA_WEBRTC}/{camera_id}/whep/{session}")
        except httpx.HTTPError:
            pass
