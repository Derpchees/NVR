"""Camera controls over ONVIF: capabilities, PTZ/zoom and day/night filter."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import db, onvif_service
from ..schemas import AuxCommandInput, IRCutInput, PTZMoveInput, RelayInput

router = APIRouter(prefix="/api/cameras/{camera_id}")


@router.get("/controls")
def camera_controls(camera_id: str):
    """Capabilities the camera really advertises, plus current day/night mode."""
    camera = db.get_camera(camera_id)
    try:
        caps = onvif_service.capabilities(camera)
        current_ir = onvif_service.ir_cut_mode(camera) if caps["ir_cut_modes"] else None
    except Exception as exc:
        onvif_service.forget(camera_id)
        raise HTTPException(status_code=502, detail="No se pudieron consultar los controles ONVIF") from exc
    return {**caps, "ir_cut_mode": current_ir}


@router.put("/relays/{token}")
async def set_relay(camera_id: str, token: str, command: RelayInput):
    camera = db.get_camera(camera_id)
    caps = await asyncio.to_thread(onvif_service.capabilities, camera)
    if not any(relay["token"] == token for relay in caps["relays"]):
        raise HTTPException(status_code=404, detail="La cámara no tiene esa salida")
    device = onvif_service.session(camera)["device"]
    try:
        await asyncio.to_thread(
            device.devicemgmt.SetRelayOutputState,
            {"RelayOutputToken": token, "LogicalState": "active" if command.active else "inactive"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="La cámara rechazó el cambio de la salida") from exc
    return {"token": token, "active": command.active}


@router.post("/aux")
async def send_aux_command(camera_id: str, command: AuxCommandInput):
    camera = db.get_camera(camera_id)
    caps = await asyncio.to_thread(onvif_service.capabilities, camera)
    if command.command not in caps["aux_commands"]:
        raise HTTPException(status_code=404, detail="La cámara no anuncia ese comando")
    session = onvif_service.session(camera)
    try:
        await asyncio.to_thread(
            session["ptz"].SendAuxiliaryCommand,
            {"ProfileToken": session["ptz_token"], "AuxiliaryData": command.command},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="La cámara rechazó el comando") from exc
    return {"command": command.command}


@router.post("/ptz/move")
async def move_ptz(camera_id: str, command: PTZMoveInput):
    if command.pan == 0 and command.tilt == 0 and command.zoom == 0:
        raise HTTPException(status_code=422, detail="Indica una dirección o zoom")
    camera = db.get_camera(camera_id)
    try:
        session = await asyncio.to_thread(onvif_service.session, camera)
        service = session["ptz"]
        if service is None:
            raise HTTPException(status_code=409, detail="La cámara no ofrece PTZ")
        previous = onvif_service.PTZ_STOP_TASKS.pop(camera_id, None)
        if previous:
            # A new ContinuousMove replaces the running one; no Stop round trip needed.
            previous.cancel()
        request = service.create_type("ContinuousMove")
        request.ProfileToken = session["ptz_token"]
        request.Velocity = {
            "PanTilt": {"x": command.pan, "y": command.tilt},
            "Zoom": {"x": command.zoom},
        }
        request.Timeout = f"PT{command.duration_ms / 1000:.3f}S"
        # Commands run in arrival order so a release (Stop) never overtakes its Move.
        async with onvif_service.ptz_lock(camera_id):
            await asyncio.to_thread(service.ContinuousMove, request)
        onvif_service.PTZ_STOP_TASKS[camera_id] = asyncio.create_task(
            onvif_service.stop_ptz_later(camera_id, command.duration_ms / 1000 + 0.2)
        )
        return {"status": "moving"}
    except HTTPException:
        raise
    except Exception as exc:
        onvif_service.forget(camera_id)
        raise HTTPException(status_code=502, detail="La cámara rechazó el movimiento PTZ") from exc


@router.post("/ptz/stop")
async def stop_ptz(camera_id: str):
    db.get_camera(camera_id)
    task = onvif_service.PTZ_STOP_TASKS.pop(camera_id, None)
    if task:
        task.cancel()
    try:
        async with onvif_service.ptz_lock(camera_id):
            await asyncio.to_thread(onvif_service.ptz_stop, camera_id)
    except Exception as exc:
        onvif_service.forget(camera_id)
        raise HTTPException(status_code=502, detail="No se pudo detener el movimiento PTZ") from exc
    return {"status": "stopped"}


@router.put("/ir-cut")
async def set_ir_cut(camera_id: str, command: IRCutInput):
    camera = db.get_camera(camera_id)
    caps = await asyncio.to_thread(onvif_service.capabilities, camera)
    if "ir_cut" in caps["ignored"]:
        raise HTTPException(status_code=409, detail="Esta cámara ignora el cambio día/noche por ONVIF")
    try:
        session = await asyncio.to_thread(onvif_service.session, camera)
        if not session["source_token"]:
            raise HTTPException(status_code=409, detail="La cámara no informa una fuente de imagen ONVIF")
        imaging = session["device"].create_imaging_service()
        token = {"VideoSourceToken": session["source_token"]}
        options = await asyncio.to_thread(imaging.GetOptions, token)
        if command.mode not in list(getattr(options, "IrCutFilterModes", []) or []):
            raise HTTPException(status_code=409, detail="Ese modo de visión nocturna no está disponible")
        await asyncio.to_thread(
            imaging.SetImagingSettings,
            {**token, "ImagingSettings": {"IrCutFilter": command.mode}, "ForcePersistence": True},
        )
        return {"ir_cut_mode": command.mode}
    except HTTPException:
        raise
    except Exception as exc:
        onvif_service.forget(camera_id)
        raise HTTPException(status_code=502, detail="No se pudo cambiar el filtro día/noche ONVIF") from exc
