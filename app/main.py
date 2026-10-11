from __future__ import annotations

import base64
import asyncio
import hmac
import ipaddress
import os
import socket
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from onvif import ONVIFCamera
from pydantic import BaseModel, Field, field_validator


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "cameras.sqlite3"
KEY_PATH = DATA_DIR / "master.key"
MEDIA_API = os.environ.get("MEDIAMTX_API", "http://127.0.0.1:9997")
MEDIA_HLS = os.environ.get("MEDIAMTX_HLS", "http://127.0.0.1:8888")
MEDIA_HLS_SECRET = os.environ.get("MEDIAMTX_HLS_SECRET", "")
WSDL_DIR = str(Path(__import__("onvif").__file__).parent.parent / "wsdl")
MAX_DISCOVERY_SECONDS = 4
PTZ_STOP_TASKS: dict[str, asyncio.Task] = {}
PLAYER_EVENTS: dict[str, deque[dict[str, object]]] = {}
PLAYER_EVENT_LIMIT = 50

app = FastAPI(title="NVR CCTV", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class CameraInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    host: str
    port: int = Field(default=80, ge=1, le=65535)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    profile_token: str | None = Field(default=None, max_length=256)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El nombre es obligatorio")
        return value

    @field_validator("host")
    @classmethod
    def private_ip_only(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise ValueError("Introduce la dirección IP local de la cámara") from exc
        if not address.is_private or address.is_loopback or address.is_multicast:
            raise ValueError("Solo se permiten direcciones IP privadas")
        return str(address)


class PTZMoveInput(BaseModel):
    pan: float = Field(default=0, ge=-1, le=1)
    tilt: float = Field(default=0, ge=-1, le=1)
    zoom: float = Field(default=0, ge=-1, le=1)
    duration_ms: int = Field(default=650, ge=100, le=3000)


class IRCutInput(BaseModel):
    mode: str = Field(pattern="^(ON|OFF|AUTO)$")


class ProfileInput(BaseModel):
    profile_token: str = Field(min_length=1, max_length=256)


class PlayerEventInput(BaseModel):
    event: str = Field(min_length=1, max_length=48)
    detail: str = Field(default="", max_length=120)
    fatal: bool = False
    media_time: float | None = Field(default=None, ge=0, le=31_536_000)
    latency: float | None = Field(default=None, ge=0, le=3_600)


def _cipher() -> Fernet:
    if not KEY_PATH.exists():
        key = Fernet.generate_key()
        try:
            descriptor = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as key_file:
                key_file.write(key)
        except FileExistsError:
            pass
    return Fernet(KEY_PATH.read_bytes())


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _init_db() -> None:
    with _connect() as connection:
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


@app.on_event("startup")
def startup() -> None:
    if not os.environ.get("APP_PASSWORD"):
        raise RuntimeError("APP_PASSWORD must be set before starting the CCTV panel")
    if not MEDIA_HLS_SECRET:
        raise RuntimeError("MEDIAMTX_HLS_SECRET must be set before starting the CCTV panel")
    _cipher()
    _init_db()


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)

    user = os.environ.get("APP_USER", "admin")
    password = os.environ.get("APP_PASSWORD", "")
    authorization = request.headers.get("authorization", "")
    valid = False
    if authorization.startswith("Basic "):
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            supplied_user, supplied_password = decoded.split(":", 1)
            valid = hmac.compare_digest(supplied_user, user) and hmac.compare_digest(
                supplied_password, password
            )
        except (ValueError, UnicodeDecodeError):
            valid = False
    if not valid:
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="CCTV", charset="UTF-8"'},
        )
    return await call_next(request)


def _get_camera(camera_id: str) -> sqlite3.Row:
    with _connect() as connection:
        camera = connection.execute(
            "SELECT * FROM cameras WHERE id = ?", (camera_id,)
        ).fetchone()
    if camera is None:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    return camera


def _camera_password(camera: sqlite3.Row) -> str:
    try:
        return _cipher().decrypt(camera["password_enc"].encode()).decode()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="No se pudo descifrar la credencial") from exc


def _onvif(host: str, port: int, username: str, password: str) -> ONVIFCamera:
    return ONVIFCamera(host, port, username, password, WSDL_DIR)


def _profiles(
    host: str, port: int, username: str, password: str
) -> list[dict[str, str | int | bool]]:
    camera = _onvif(host, port, username, password)
    service = camera.create_media_service()
    onvif_profiles = service.GetProfiles()
    if not onvif_profiles:
        raise ValueError("La cámara no ofrece perfiles de vídeo ONVIF")
    profiles = []
    for profile in onvif_profiles:
        encoder = getattr(profile, "VideoEncoderConfiguration", None)
        resolution = getattr(encoder, "Resolution", None) if encoder else None
        rate_control = getattr(encoder, "RateControl", None) if encoder else None
        try:
            width = int(getattr(resolution, "Width", 0) or 0)
            height = int(getattr(resolution, "Height", 0) or 0)
        except (TypeError, ValueError):
            width, height = 0, 0
        profiles.append(
            {
                "token": profile.token,
                "name": getattr(profile, "Name", "Perfil") or "Perfil",
                "width": width,
                "height": height,
                "encoding": str(getattr(encoder, "Encoding", "") or ""),
                "frame_rate": int(getattr(rate_control, "FrameRateLimit", 0) or 0),
            }
        )
    profiles.sort(key=lambda item: item["width"] * item["height"], reverse=True)
    for index, profile in enumerate(profiles):
        profile["recommended"] = index == 0
    return profiles


def _camera_services(camera: sqlite3.Row):
    device = _onvif(
        camera["host"], camera["port"], camera["username"], _camera_password(camera)
    )
    media = device.create_media_service()
    profile = next(
        (item for item in media.GetProfiles() if item.token == camera["profile_token"]),
        None,
    )
    sources = media.GetVideoSources()
    source_token = sources[0].token if sources else None
    return device, media, profile, source_token


def _ptz_stop(camera_id: str) -> None:
    camera = _get_camera(camera_id)
    device, _, profile, _ = _camera_services(camera)
    if not profile or not getattr(profile, "PTZConfiguration", None):
        raise ValueError("El perfil ONVIF seleccionado no tiene PTZ")
    service = device.create_ptz_service()
    request = service.create_type("Stop")
    request.ProfileToken = profile.token
    request.PanTilt = True
    request.Zoom = True
    service.Stop(request)


async def _stop_ptz_later(camera_id: str, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        await asyncio.to_thread(_ptz_stop, camera_id)
    except asyncio.CancelledError:
        return
    except Exception:
        pass
    finally:
        if PTZ_STOP_TASKS.get(camera_id) is asyncio.current_task():
            PTZ_STOP_TASKS.pop(camera_id, None)


def _stream_uri(camera: sqlite3.Row, password: str) -> str:
    device = _onvif(camera["host"], camera["port"], camera["username"], password)
    media = device.create_media_service()
    request = media.create_type("GetStreamUri")
    request.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
    request.ProfileToken = camera["profile_token"]
    result = media.GetStreamUri(request)
    if not result or not result.Uri:
        raise ValueError("La cámara no devolvió una dirección RTSP")
    return result.Uri


def _with_credentials(uri: str, username: str, password: str) -> str:
    parts = urlsplit(uri)
    if not username and not password:
        return uri
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    user_info = f"{quote(username, safe="")}" + f":{quote(password, safe="")}@"
    return urlunsplit((parts.scheme, user_info + host, parts.path, parts.query, parts.fragment))


def _profile_diagnostics(camera: sqlite3.Row) -> dict[str, object]:
    """Read the selected ONVIF profile without returning connection credentials."""
    _, _, profile, _ = _camera_services(camera)
    if profile is None:
        return {"available": False}
    encoder = getattr(profile, "VideoEncoderConfiguration", None)
    resolution = getattr(encoder, "Resolution", None) if encoder else None
    rate_control = getattr(encoder, "RateControl", None) if encoder else None
    return {
        "available": True,
        "encoding": getattr(encoder, "Encoding", None),
        "width": getattr(resolution, "Width", None),
        "height": getattr(resolution, "Height", None),
        "frame_rate_limit": getattr(rate_control, "FrameRateLimit", None),
        "bitrate_limit": getattr(rate_control, "BitrateLimit", None),
    }


def _playlist_summary(playlist: str) -> dict[str, object]:
    lines = [line.strip() for line in playlist.splitlines() if line.startswith("#EXT-X-")]
    return {
        "part_inf": next((line for line in lines if line.startswith("#EXT-X-PART-INF:")), None),
        "server_control": next(
            (line for line in lines if line.startswith("#EXT-X-SERVER-CONTROL:")), None
        ),
        "part_count": sum(line.startswith("#EXT-X-PART:") for line in lines),
        "segment_count": sum(line.startswith("#EXTINF:") for line in lines),
    }


def _discover() -> list[dict[str, str | int]]:
    namespaces = {
        "s": "http://www.w3.org/2003/05/soap-envelope",
        "a": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
        "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
        "dn": "http://www.onvif.org/ver10/network/wsdl",
    }
    message_id = f"urn:uuid:{uuid.uuid4()}"
    envelope = ET.Element(ET.QName(namespaces["s"], "Envelope"))
    header = ET.SubElement(envelope, ET.QName(namespaces["s"], "Header"))
    ET.SubElement(header, ET.QName(namespaces["a"], "MessageID")).text = message_id
    ET.SubElement(header, ET.QName(namespaces["a"], "To")).text = (
        "urn:schemas-xmlsoap-org:ws:2005:04:discovery"
    )
    ET.SubElement(header, ET.QName(namespaces["a"], "Action")).text = (
        "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe"
    )
    body = ET.SubElement(envelope, ET.QName(namespaces["s"], "Body"))
    probe = ET.SubElement(body, ET.QName(namespaces["d"], "Probe"))
    ET.SubElement(probe, ET.QName(namespaces["d"], "Types")).text = (
        "dn:NetworkVideoTransmitter"
    )
    payload = ET.tostring(envelope, encoding="utf-8", xml_declaration=True)
    found: dict[str, dict[str, str | int]] = {}

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.settimeout(0.25)
            sock.sendto(payload, ("239.255.255.250", 3702))
            deadline = __import__("time").monotonic() + MAX_DISCOVERY_SECONDS
            while __import__("time").monotonic() < deadline:
                try:
                    response, _ = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                try:
                    root = ET.fromstring(response)
                except ET.ParseError:
                    continue
                xaddrs = root.findtext(".//{*}XAddrs", default="")
                scopes = root.findtext(".//{*}Scopes", default="")
                if not xaddrs:
                    continue
                for xaddr in xaddrs.split():
                    parsed = urlsplit(xaddr)
                    try:
                        address = ipaddress.ip_address(parsed.hostname or "")
                    except ValueError:
                        continue
                    if not address.is_private or address.is_loopback or address.is_multicast:
                        continue
                    host = str(address)
                    name = next(
                        (
                            scope.rsplit("/", 1)[-1]
                            for scope in scopes.split()
                            if "/name/" in scope
                        ),
                        "Cámara ONVIF",
                    )
                    found[host] = {
                        "host": host,
                        "port": parsed.port or 80,
                        "name": name,
                    }
                    break
    except OSError as exc:
        raise HTTPException(status_code=503, detail="No se pudo enviar el anuncio ONVIF multicast") from exc
    return list(found.values())


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/cameras")
def list_cameras():
    with _connect() as connection:
        cameras = connection.execute(
            "SELECT id, name, host, port, username, profile_token FROM cameras ORDER BY created_at, name"
        ).fetchall()
    return [dict(camera) for camera in cameras]


@app.get("/api/cameras/{camera_id}/profiles")
def camera_profiles(camera_id: str):
    camera = _get_camera(camera_id)
    try:
        profiles = _profiles(
            camera["host"], camera["port"], camera["username"], _camera_password(camera)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudieron consultar los perfiles ONVIF") from exc
    for profile in profiles:
        profile["selected"] = profile["token"] == camera["profile_token"]
    return {"profiles": profiles}


@app.put("/api/cameras/{camera_id}/profile")
async def select_camera_profile(camera_id: str, command: ProfileInput):
    """Switch only the NVR stream profile; this does not change camera image settings."""
    camera = _get_camera(camera_id)
    password = _camera_password(camera)
    try:
        profiles = _profiles(camera["host"], camera["port"], camera["username"], password)
        if not any(profile["token"] == command.profile_token for profile in profiles):
            raise HTTPException(status_code=409, detail="El perfil ya no está disponible en la cámara")
        _stream_uri({**dict(camera), "profile_token": command.profile_token}, password)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo validar el perfil RTSP seleccionado") from exc

    with _connect() as connection:
        connection.execute(
            "UPDATE cameras SET profile_token = ? WHERE id = ?",
            (command.profile_token, camera_id),
        )
    async with httpx.AsyncClient(timeout=3) as client:
        try:
            await client.delete(f"{MEDIA_API}/v3/config/paths/delete/{camera_id}")
        except httpx.HTTPError:
            pass
    return {"profile_token": command.profile_token}


@app.post("/api/cameras/discover")
def discover_cameras():
    return _discover()


@app.post("/api/cameras/test")
def test_camera(camera: CameraInput):
    try:
        profiles = _profiles(camera.host, camera.port, camera.username, camera.password)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="No se pudo consultar ONVIF; verifica IP, puerto y credenciales",
        ) from exc
    return {"profiles": profiles}


@app.post("/api/cameras", status_code=201)
def add_camera(camera: CameraInput):
    try:
        profiles = _profiles(camera.host, camera.port, camera.username, camera.password)
        selected = next(
            (item for item in profiles if item["token"] == camera.profile_token),
            profiles[0],
        )
        row = {
            "id": str(uuid.uuid4()),
            "host": camera.host,
            "port": camera.port,
            "username": camera.username,
            "password_enc": _cipher().encrypt(camera.password.encode()).decode(),
            "profile_token": selected["token"],
        }
        _stream_uri(
            {
                **row,
                "password_enc": row["password_enc"],
            },
            camera.password,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="No se pudo validar ONVIF y obtener el perfil RTSP",
        ) from exc
    with _connect() as connection:
        connection.execute(
            "INSERT INTO cameras (id, name, host, port, username, password_enc, profile_token) "
            "VALUES (:id, :name, :host, :port, :username, :password_enc, :profile_token)",
            {**row, "name": camera.name},
        )
    return {
        "id": row["id"],
        "name": camera.name,
        "host": camera.host,
        "port": camera.port,
        "profile_token": selected["token"],
    }


@app.delete("/api/cameras/{camera_id}", status_code=204)
async def delete_camera(camera_id: str):
    _get_camera(camera_id)
    async with httpx.AsyncClient(timeout=3) as client:
        try:
            await client.delete(f"{MEDIA_API}/v3/config/paths/delete/{camera_id}")
        except httpx.HTTPError:
            pass
    with _connect() as connection:
        connection.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
    PLAYER_EVENTS.pop(camera_id, None)
    return Response(status_code=204)


@app.get("/api/cameras/{camera_id}/controls")
def camera_controls(camera_id: str):
    camera = _get_camera(camera_id)
    try:
        device, _, profile, source_token = _camera_services(camera)
        ptz_supported = False
        try:
            device.create_ptz_service().GetNodes()
            ptz_supported = bool(profile and getattr(profile, "PTZConfiguration", None))
        except Exception:
            ptz_supported = False

        ir_cut_modes: list[str] = []
        ir_cut_mode = None
        if source_token:
            try:
                imaging = device.create_imaging_service()
                settings = imaging.GetImagingSettings({"VideoSourceToken": source_token})
                ir_cut_mode = getattr(settings, "IrCutFilter", None)
                options = imaging.GetOptions({"VideoSourceToken": source_token})
                ir_cut_modes = list(getattr(options, "IrCutFilterModes", []) or [])
            except Exception:
                pass
        return {
            "ptz": ptz_supported,
            "zoom": ptz_supported,
            "ir_cut_modes": ir_cut_modes,
            "ir_cut_mode": ir_cut_mode,
            "white_light": False,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudieron consultar los controles ONVIF") from exc


@app.post("/api/cameras/{camera_id}/ptz/move")
async def move_ptz(camera_id: str, command: PTZMoveInput):
    if command.pan == 0 and command.tilt == 0 and command.zoom == 0:
        raise HTTPException(status_code=422, detail="Indica una dirección o zoom")
    camera = _get_camera(camera_id)
    try:
        device, _, profile, _ = _camera_services(camera)
        if not profile or not getattr(profile, "PTZConfiguration", None):
            raise HTTPException(status_code=409, detail="El perfil seleccionado no ofrece PTZ")
        previous = PTZ_STOP_TASKS.get(camera_id)
        if previous:
            previous.cancel()
            await asyncio.to_thread(_ptz_stop, camera_id)
        service = device.create_ptz_service()
        request = service.create_type("ContinuousMove")
        request.ProfileToken = profile.token
        request.Velocity = {
            "PanTilt": {"x": command.pan, "y": command.tilt},
            "Zoom": {"x": command.zoom},
        }
        request.Timeout = f"PT{command.duration_ms / 1000:.3f}S"
        await asyncio.to_thread(service.ContinuousMove, request)
        task = asyncio.create_task(
            _stop_ptz_later(camera_id, command.duration_ms / 1000 + 0.2)
        )
        PTZ_STOP_TASKS[camera_id] = task
        return {"status": "moving"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="La cámara rechazó el movimiento PTZ") from exc


@app.post("/api/cameras/{camera_id}/ptz/stop")
async def stop_ptz(camera_id: str):
    _get_camera(camera_id)
    task = PTZ_STOP_TASKS.pop(camera_id, None)
    if task:
        task.cancel()
    try:
        await asyncio.to_thread(_ptz_stop, camera_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo detener el movimiento PTZ") from exc
    return {"status": "stopped"}


@app.put("/api/cameras/{camera_id}/ir-cut")
async def set_ir_cut(camera_id: str, command: IRCutInput):
    camera = _get_camera(camera_id)
    try:
        device, _, _, source_token = _camera_services(camera)
        if not source_token:
            raise HTTPException(status_code=409, detail="La cámara no informa una fuente de imagen ONVIF")
        imaging = device.create_imaging_service()
        options = imaging.GetOptions({"VideoSourceToken": source_token})
        modes = list(getattr(options, "IrCutFilterModes", []) or [])
        if command.mode not in modes:
            raise HTTPException(status_code=409, detail="Ese modo de visión nocturna no está disponible")
        imaging.SetImagingSettings(
            {
                "VideoSourceToken": source_token,
                "ImagingSettings": {"IrCutFilter": command.mode},
                "ForcePersistence": True,
            }
        )
        return {"ir_cut_mode": command.mode}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo cambiar el filtro día/noche ONVIF") from exc


@app.post("/api/cameras/{camera_id}/playback-events", status_code=204)
def record_playback_event(camera_id: str, event: PlayerEventInput):
    """Keep a short, credential-free window of browser playback observations."""
    _get_camera(camera_id)
    events = PLAYER_EVENTS.setdefault(camera_id, deque(maxlen=PLAYER_EVENT_LIMIT))
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


@app.get("/api/cameras/{camera_id}/diagnostics")
async def camera_diagnostics(camera_id: str):
    """Return live-safe observations for delay/freeze investigations.

    Source URLs are intentionally never included: they can contain camera credentials.
    """
    camera = _get_camera(camera_id)
    path_state: dict[str, object] = {"available": False}
    playlist: dict[str, object] = {"available": False}

    # MediaMTX may wait up to sourceOnDemandStartTimeout (8 s) for RTSP.
    # Keep this longer than that window so diagnostics do not misreport a
    # healthy on-demand source as unavailable.
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(f"{MEDIA_API}/v3/paths/get/{camera_id}")
            if response.status_code == 200:
                payload = response.json()
                path_state = {
                    "available": True,
                    "ready": payload.get("ready"),
                    "source_ready": payload.get("sourceReady"),
                    "readers": len(payload.get("readers", [])),
                    "tracks": payload.get("tracks", []),
                }
            else:
                path_state = {"available": False, "status_code": response.status_code}
        except (httpx.HTTPError, ValueError):
            path_state = {"available": False, "error": "MediaMTX no respondió"}

        try:
            response = await client.get(
                f"{MEDIA_HLS}/{camera_id}/index.m3u8",
                headers={"Authorization": f"Bearer {MEDIA_HLS_SECRET}"},
            )
            if response.status_code == 200:
                playlist = {"available": True, **_playlist_summary(response.text)}
            else:
                playlist = {"available": False, "status_code": response.status_code}
        except httpx.HTTPError:
            playlist = {"available": False, "error": "Playlist HLS no disponible"}

    try:
        profile = await asyncio.to_thread(_profile_diagnostics, camera)
    except Exception:
        profile = {"available": False, "error": "No se pudo consultar el perfil ONVIF"}

    return {
        "profile": profile,
        "mediamtx": path_state,
        "hls": playlist,
        "browser_events": list(PLAYER_EVENTS.get(camera_id, ())),
    }


@app.post("/api/cameras/{camera_id}/stream")
async def start_stream(camera_id: str):
    camera = _get_camera(camera_id)
    password = _camera_password(camera)
    try:
        uri = _with_credentials(
            _stream_uri(camera, password), camera["username"], password
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo solicitar la URL RTSP a la cámara") from exc

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            current = await client.get(f"{MEDIA_API}/v3/config/paths/get/{camera_id}")
            endpoint = (
                f"{MEDIA_API}/v3/config/paths/replace/{camera_id}"
                if current.status_code == 200
                else f"{MEDIA_API}/v3/config/paths/add/{camera_id}"
            )
            result = await client.post(
                endpoint,
                json={
                    "source": uri,
                    "sourceOnDemand": True,
                    "sourceOnDemandStartTimeout": "8s",
                    "rtspTransport": "tcp",
                },
            )
            result.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="MediaMTX no está disponible") from exc
    return {"playlist": f"/api/cameras/{camera_id}/stream/index.m3u8"}


@app.get("/api/cameras/{camera_id}/stream/{asset}")
async def stream_asset(camera_id: str, asset: str):
    _get_camera(camera_id)
    if not asset or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in asset):
        raise HTTPException(status_code=404, detail="Recurso no encontrado")
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            upstream_url = f"{MEDIA_HLS}/{camera_id}/{asset}"
            upstream = await client.get(
                upstream_url,
                headers={"Authorization": f"Bearer {MEDIA_HLS_SECRET}"},
            )
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
    content_type = upstream.headers.get("content-type", "application/octet-stream")
    response = Response(
        content=upstream.content,
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
    )
    return response
