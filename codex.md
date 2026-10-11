# Codex Guide: NVR CCTV

## Project Goal

Maintain a Spanish-language, self-hosted CCTV dashboard for ONVIF/RTSP cameras. The user prefers the look and layout of the supplied MCServer panel reference, but the product identity must remain NVR/CCTV. Keep the interface practical and readable; do not add Minecraft/voxel branding or pixel-art styling.

## Architecture

- `app/main.py`: FastAPI app, HTTP Basic authentication, encrypted SQLite camera credentials, ONVIF discovery/profile lookup, PTZ/imaging endpoints, RTSP source setup, and authenticated HLS proxy.
- `app/static/index.html`: Spanish dashboard and camera dialogs.
- `app/static/cctv.css`: NVR/CCTV styling inspired by the reference MCServer layout.
- `app/static/app.js`: camera cards, ONVIF form, HLS.js playback, PTZ and imaging controls.
- `Dockerfile`: Python application image; bundles HLS.js locally.
- `docker-compose.yml`: app and MediaMTX, both on host networking so ONVIF multicast and camera streams can reach the LAN.
- `mediamtx.yml`: MediaMTX API/HLS listeners bound to loopback; HLS uses LL-HLS settings and a CDN bearer secret supplied through `.env`.
- Persistent camera database and encryption key live in the `cctv-data` Docker volume. Do not remove that volume during redeploys.

## Verified Deployment

- Ubuntu server: `server@192.168.3.3`
- Project directory: `~/nvr-cctv`
- Dashboard: `http://192.168.3.3:8080`
- Camera: `192.168.3.51`, ONVIF HTTP port `80`, RTSP port `554`
- Compose project has services `cctv` and `mediamtx`.

Deploy from the server after transferring changed files to their exact destination paths:

```sh
cd ~/nvr-cctv
docker compose config -q
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
```

When copying from Windows, use one `scp` command per destination file if files belong in different subdirectories. A multi-source `scp` command can place files at an unintended common destination. Never print or commit `.env`, camera passwords, `APP_PASSWORD`, `HLS_CDN_SECRET`, or the volume encryption key.

## Camera Facts

- The camera is managed by HiEasy/P6SLite/EasyVMS and supports a P6S P2P path. P6S discovery is not ONVIF WS-Discovery; a failed multicast search does not prove ONVIF is absent.
- Direct ONVIF queries have succeeded and returned two profiles: `PROFILE_000` (2304x1296) and `PROFILE_001` (800x448). The UI should recommend the higher-resolution profile, while keeping manual selection available.
- ONVIF reports PTZ and zoom velocity spaces, and Imaging reports `IrCutFilterModes` `ON`, `OFF`, `AUTO`.
- White-light control was not advertised by ONVIF. Do not display or claim white-light control unless a supported ONVIF/vendor API is verified.
- Codec metadata has been inconsistent: ONVIF profile metadata reported H.264 in a recent probe, while MediaMTX reported the active RTSP stream as H.265. Re-query both the selected profile and active stream before changing codecs or blaming browser support.
- The camera's RTSP URI is provided by ONVIF; do not hard-code an RTSP path unless ONVIF stops returning one.

## Current Playback Design

- Camera streams are pulled by MediaMTX over RTSP/TCP and served to the browser as HLS.
- The browser uses local HLS.js with `lowLatencyMode: true`, muted autoplay, and custom playback buttons (not native Chrome controls).
- LL-HLS is configured in `mediamtx.yml`: `hlsVariant: lowLatency`, `hlsSegmentDuration: 1s`, `hlsPartDuration: 200ms`, `hlsSegmentCount: 7`.
- Effective playlists have shown approximately 333 ms parts and `PART-HOLD-BACK` around 835 ms; the camera has reported 6 FPS. This suggests remaining latency may be caused by camera frame/keyframe cadence, HLS.js live-edge settings, decode performance, or the camera's own processing rather than the configured HLS target alone.
- MediaMTX's HLS CDN bearer mode is enabled. The browser must only call the app's authenticated `/api/cameras/{id}/stream/...` proxy; do not expose MediaMTX API/HLS ports to the LAN or remove bearer auth.

## Immediate Work Queue

1. Diagnose reported excessive delay and intermittent freezes. Capture live MediaMTX path state, current codec/FPS if available, HLS playlist part/hold-back values, and HLS.js fatal/nonfatal events while the user observes playback. Distinguish source stalls from browser decode stalls.
2. Verify the camera's profile and actual RTSP stream codec again. If the main stream is HEVC and browser decode is unreliable, prefer testing the 800x448 substream if it is H.264. Do not add transcoding until direct stream evidence shows it is needed; transcoding costs CPU and adds delay.
3. Correct the day/night selector labels. ONVIF `IrCutFilter` values describe the IR-cut filter state; verify the ON/OFF meanings, then label them accurately as day/night/automatic. Current UI mapping may be reversed: it currently maps `ON` to “Noche” and `OFF` to “Día”.
4. PTZ controls use ONVIF `ContinuousMove` and automatic stop. Test movement only when the user asks or when it can be tested safely; never leave a movement running. The camera has zoom capability.
5. Keep white-light control hidden unless a documented and authorized protocol/API is found. P6S is proprietary/P2P; do not reverse-engineer or claim it is integrated based only on the EasyVMS app working.

## Safety and Product Rules

- Keep camera actions authenticated by the panel and restricted to configured private LAN IPs.
- Do not expose secrets in logs, tool output, screenshots, documentation, or source control. If a camera or panel password was posted in chat, recommend rotating it after testing.
- Do not factory-reset the camera, change its network/port settings, or change image settings without explicit user approval.
- Use only ONVIF operations that the camera advertises. Report unsupported features rather than rendering controls that appear to work but do nothing.
- Preserve camera data and the volume across rebuilds. Never use destructive Docker volume removal as a troubleshooting shortcut.

## Validation

After Python changes:

```powershell
python -m py_compile app/main.py
```

After browser JavaScript changes:

```powershell
node --check app/static/app.js
```

Then validate Compose and verify `/healthz`, authenticated app HTTP 200, MediaMTX `ready`/tracks, and one HLS variant/part request through the panel proxy. A successful HTTP response alone does not prove the browser decodes the codec; check HLS.js/browser media errors and actual playback state.