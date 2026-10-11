"""ONVIF access: profiles, cached device sessions, PTZ and WS-Discovery."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import sqlite3
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import onvif
from onvif import ONVIFCamera

from . import config, db

WSDL_DIR = str(Path(onvif.__file__).parent.parent / "wsdl")

# Building an ONVIF client and listing profiles costs ~1 s on this camera, so
# control commands reuse one session per camera instead of paying it per click.
SESSIONS: dict[str, dict[str, object]] = {}
PTZ_LOCKS: dict[str, asyncio.Lock] = {}
PTZ_STOP_TASKS: dict[str, asyncio.Task] = {}
CONNECT_LOCKS: dict[str, threading.Lock] = {}


def connect(host: str, port: int, username: str, password: str) -> ONVIFCamera:
    # Some cameras answer concurrent session setups with a generic SOAP fault,
    # so handshakes to the same device are serialised.
    with CONNECT_LOCKS.setdefault(host, threading.Lock()):
        return ONVIFCamera(host, port, username, password, WSDL_DIR)


def profiles(host: str, port: int, username: str, password: str) -> list[dict[str, object]]:
    return rank_profiles(connect(host, port, username, password).create_media_service().GetProfiles())


def rank_profiles(onvif_profiles) -> list[dict[str, object]]:
    """Summarise profiles, highest resolution first."""
    if not onvif_profiles:
        raise ValueError("La cámara no ofrece perfiles de vídeo ONVIF")
    ranked = []
    for profile in onvif_profiles:
        encoder = getattr(profile, "VideoEncoderConfiguration", None)
        resolution = getattr(encoder, "Resolution", None) if encoder else None
        rate_control = getattr(encoder, "RateControl", None) if encoder else None
        try:
            width = int(getattr(resolution, "Width", 0) or 0)
            height = int(getattr(resolution, "Height", 0) or 0)
        except (TypeError, ValueError):
            width, height = 0, 0
        ranked.append(
            {
                "token": profile.token,
                "name": getattr(profile, "Name", "Perfil") or "Perfil",
                "width": width,
                "height": height,
                "encoding": str(getattr(encoder, "Encoding", "") or ""),
                "frame_rate": int(getattr(rate_control, "FrameRateLimit", 0) or 0),
            }
        )
    ranked.sort(key=lambda item: item["width"] * item["height"], reverse=True)
    return ranked


def best_stream(host: str, port: int, username: str, password: str) -> tuple[str, str]:
    """Return (profile token, RTSP URI) for the highest-resolution profile."""
    media = connect(host, port, username, password).create_media_service()
    best = rank_profiles(media.GetProfiles())[0]
    request = media.create_type("GetStreamUri")
    request.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
    request.ProfileToken = best["token"]
    result = media.GetStreamUri(request)
    if not result or not result.Uri:
        raise ValueError("La cámara no devolvió una dirección RTSP")
    return str(best["token"]), result.Uri


def with_credentials(uri: str, username: str, password: str) -> str:
    if not username and not password:
        return uri
    parts = urlsplit(uri)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    user_info = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    return urlunsplit((parts.scheme, user_info + host, parts.path, parts.query, parts.fragment))


def session(camera: sqlite3.Row) -> dict[str, object]:
    cached = SESSIONS.get(camera["id"])
    if cached and cached["profile_token"] == camera["profile_token"]:
        return cached
    device = connect(camera["host"], camera["port"], camera["username"], db.camera_password(camera))
    media = device.create_media_service()
    all_profiles = media.GetProfiles()
    profile = next((item for item in all_profiles if item.token == camera["profile_token"]), None)
    # PTZ belongs to the device, not the stream; use any profile that carries it.
    ptz_profile = next(
        (
            item
            for item in [profile, *all_profiles]
            if item is not None and getattr(item, "PTZConfiguration", None)
        ),
        None,
    )
    sources = media.GetVideoSources()
    entry: dict[str, object] = {
        "profile_token": camera["profile_token"],
        "device": device,
        "profile": profile,
        "source_token": sources[0].token if sources else None,
        "ptz": device.create_ptz_service() if ptz_profile else None,
        "ptz_token": ptz_profile.token if ptz_profile else None,
    }
    SESSIONS[camera["id"]] = entry
    return entry


def forget(camera_id: str) -> None:
    SESSIONS.pop(camera_id, None)


def _safe(call, default=None):
    """Optional ONVIF features answer with faults when absent; treat as missing."""
    try:
        return call()
    except Exception:
        return default


# Imaging settings the panel can describe; values are ONVIF option names.
IMAGE_ADJUSTMENTS = {
    "Brightness": "Brillo",
    "Contrast": "Contraste",
    "ColorSaturation": "Saturación",
    "Sharpness": "Nitidez",
    "WideDynamicRange": "WDR",
    "BacklightCompensation": "Contraluz",
    "WhiteBalance": "Balance de blancos",
    "Focus": "Enfoque",
}


# Features a model advertises over ONVIF but does not honour, keyed by model
# prefix. Only add entries verified against a real device.
MODEL_QUIRKS: dict[str, dict[str, object]] = {
    # Verified 2026-10-11 (firmware 1.4.0): IrCutFilter changes are accepted
    # and reported back but the image stays in night mode; RELAY_000 toggles
    # with no visible effect. Day/night, white light and the speaker are only
    # reachable through the vendor's proprietary P6S protocol (EasyVMS).
    "P3LA-52PHM": {
        "ignored": ["ir_cut", "relays"],
        "note": "Este modelo ignora día/noche por ONVIF; la luz y la bocina solo se controlan desde EasyVMS.",
    },
}


def _apply_quirks(caps: dict[str, object]) -> dict[str, object]:
    model = str(caps["device"]["model"] or "")
    quirk = next((value for prefix, value in MODEL_QUIRKS.items() if model.startswith(prefix)), None)
    caps["ignored"] = []
    caps["notes"] = []
    if not quirk:
        return caps
    if "ir_cut" in quirk["ignored"] and caps["ir_cut_modes"]:
        caps["ir_cut_modes"] = []
        caps["ignored"].append("ir_cut")
    if "relays" in quirk["ignored"] and caps["relays"]:
        caps["relays"] = []
        caps["ignored"].append("relays")
    caps["notes"].append(quirk["note"])
    return caps


def capabilities(camera: sqlite3.Row) -> dict[str, object]:
    """Detect what this camera really supports. Cached with the ONVIF session."""
    entry = session(camera)
    if "capabilities" in entry:
        return entry["capabilities"]
    device = entry["device"]
    media = device.create_media_service()
    profile = entry["profile"]
    source = {"VideoSourceToken": entry["source_token"]} if entry["source_token"] else None

    imaging = _safe(device.create_imaging_service)
    options = _safe(lambda: imaging.GetOptions(source)) if imaging and source else None
    ir_modes = list(getattr(options, "IrCutFilterModes", None) or [])

    node = None
    if entry["ptz"] is not None:
        nodes = _safe(entry["ptz"].GetNodes, []) or []
        node = nodes[0] if nodes else None
    aux_commands = [str(command) for command in (getattr(node, "AuxiliaryCommands", None) or [])]

    relays = [
        {
            "token": relay.token,
            "mode": str(getattr(relay.Properties, "Mode", "")),
            "idle": str(getattr(relay.Properties, "IdleState", "")),
        }
        for relay in (_safe(device.devicemgmt.GetRelayOutputs, []) or [])
    ]

    audio_encoder = getattr(profile, "AudioEncoderConfiguration", None)
    audio_outputs = _safe(media.GetAudioOutputs, []) or []
    info = _safe(device.devicemgmt.GetDeviceInformation)
    encoder = getattr(profile, "VideoEncoderConfiguration", None)
    resolution = getattr(encoder, "Resolution", None) if encoder else None

    caps = {
        "device": {
            "manufacturer": getattr(info, "Manufacturer", None),
            "model": getattr(info, "Model", None),
            "firmware": getattr(info, "FirmwareVersion", None),
        },
        "resolution": f"{resolution.Width}×{resolution.Height}" if resolution else None,
        "ptz": entry["ptz"] is not None,
        "zoom": entry["ptz"] is not None,
        "presets": int(getattr(node, "MaximumNumberOfPresets", 0) or 0),
        "home": bool(getattr(node, "HomeSupported", False)),
        "ir_cut_modes": ir_modes,
        # Lights are only controllable when the camera names them: PTZ
        # auxiliary commands such as tt:IRLamp|On or tt:Light|On.
        "light_commands": [command for command in aux_commands if "lamp" in command.lower() or "light" in command.lower()],
        "aux_commands": aux_commands,
        "relays": relays,
        "microphone": bool(getattr(profile, "AudioSourceConfiguration", None)),
        "audio_codec": str(getattr(audio_encoder, "Encoding", "") or "") or None,
        "speaker": bool(audio_outputs),
        "image": [
            label for name, label in IMAGE_ADJUSTMENTS.items() if getattr(options, name, None)
        ] if options else [],
    }
    entry["capabilities"] = _apply_quirks(caps)
    return caps


def ir_cut_mode(camera: sqlite3.Row) -> str | None:
    entry = session(camera)
    if not entry["source_token"]:
        return None
    imaging = entry["device"].create_imaging_service()
    settings = _safe(lambda: imaging.GetImagingSettings({"VideoSourceToken": entry["source_token"]}))
    return getattr(settings, "IrCutFilter", None)


def profile_summary(camera: sqlite3.Row) -> dict[str, object]:
    """The active profile as ONVIF reports it (codec metadata may be wrong)."""
    profile = session(camera)["profile"]
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


def ptz_lock(camera_id: str) -> asyncio.Lock:
    return PTZ_LOCKS.setdefault(camera_id, asyncio.Lock())


def ptz_stop(camera_id: str) -> None:
    entry = session(db.get_camera(camera_id))
    service = entry["ptz"]
    if service is None:
        raise ValueError("La cámara no ofrece PTZ")
    request = service.create_type("Stop")
    request.ProfileToken = entry["ptz_token"]
    request.PanTilt = True
    request.Zoom = True
    service.Stop(request)


async def stop_ptz_later(camera_id: str, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        async with ptz_lock(camera_id):
            await asyncio.to_thread(ptz_stop, camera_id)
    except asyncio.CancelledError:
        return
    except Exception:
        pass
    finally:
        if PTZ_STOP_TASKS.get(camera_id) is asyncio.current_task():
            PTZ_STOP_TASKS.pop(camera_id, None)


def discover() -> list[dict[str, str | int]]:
    """Send one WS-Discovery probe and collect private-network responders."""
    namespaces = {
        "s": "http://www.w3.org/2003/05/soap-envelope",
        "a": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
        "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
        "dn": "http://www.onvif.org/ver10/network/wsdl",
    }
    envelope = ET.Element(ET.QName(namespaces["s"], "Envelope"))
    header = ET.SubElement(envelope, ET.QName(namespaces["s"], "Header"))
    ET.SubElement(header, ET.QName(namespaces["a"], "MessageID")).text = f"urn:uuid:{uuid.uuid4()}"
    ET.SubElement(header, ET.QName(namespaces["a"], "To")).text = (
        "urn:schemas-xmlsoap-org:ws:2005:04:discovery"
    )
    ET.SubElement(header, ET.QName(namespaces["a"], "Action")).text = (
        "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe"
    )
    body = ET.SubElement(envelope, ET.QName(namespaces["s"], "Body"))
    probe = ET.SubElement(body, ET.QName(namespaces["d"], "Probe"))
    ET.SubElement(probe, ET.QName(namespaces["d"], "Types")).text = "dn:NetworkVideoTransmitter"
    payload = ET.tostring(envelope, encoding="utf-8", xml_declaration=True)
    found: dict[str, dict[str, str | int]] = {}

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.25)
        sock.sendto(payload, ("239.255.255.250", 3702))
        deadline = time.monotonic() + config.MAX_DISCOVERY_SECONDS
        while time.monotonic() < deadline:
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
            for xaddr in xaddrs.split():
                parsed = urlsplit(xaddr)
                try:
                    address = ipaddress.ip_address(parsed.hostname or "")
                except ValueError:
                    continue
                if not address.is_private or address.is_loopback or address.is_multicast:
                    continue
                name = next(
                    (scope.rsplit("/", 1)[-1] for scope in scopes.split() if "/name/" in scope),
                    "Cámara ONVIF",
                )
                found[str(address)] = {"host": str(address), "port": parsed.port or 80, "name": name}
                break
    return list(found.values())
