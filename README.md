# NVR CCTV

Panel web local para descubrir cámaras ONVIF, validar perfiles y ver flujos RTSP en el navegador mediante HLS.

## Requisitos

- Docker Engine y Docker Compose en un host Linux.
- Cámaras y host en una red local que permita WS-Discovery multicast UDP/3702.
- Puerto TCP 8080 accesible desde los equipos que usarán el panel.

El modo `network_mode: host` se utiliza para que el contenedor reciba WS-Discovery y alcance las cámaras en la red del host. El panel escucha en 8080. MediaMTX solo escucha en loopback, salvo el puerto **8189 (UDP y TCP)**, por el que viaja el vídeo WebRTC hacia el navegador. Si el servidor usa `ufw`, ábrelo para la red local:

```sh
sudo ufw allow from 192.168.3.0/24 to any port 8189 proto udp
sudo ufw allow from 192.168.3.0/24 to any port 8189 proto tcp
```

Sin ese puerto el panel sigue funcionando, pero cae a HLS, que tiene varios segundos de retraso.

## Inicio

```sh
cp .env.example .env
```

Edita `.env` y establece una contraseña única para el panel. Establece también `HLS_CDN_SECRET` con un secreto aleatorio largo; el mismo secreto se comparte internamente entre el panel y MediaMTX para autorizar playlists y segmentos, sin exponer la API de vídeo al navegador. Después:

```sh
docker compose up -d --build
```

Abre `http://IP_DEL_SERVIDOR:8080` e inicia sesión con los valores de `.env`. Esa cuenta se crea solo en el primer arranque; después se cambia desde la pestaña **Cuenta** y `.env` deja de usarse para el acceso. En **Cámaras** usa **Buscar en la red** o **Añadir cámara**. La cámara necesita ONVIF activado y una cuenta con acceso a sus perfiles de vídeo; el panel elige siempre el perfil de mayor resolución.

## Notas

- Las contraseñas ONVIF se cifran en la base de datos persistente de la aplicación. La clave de cifrado también queda en el volumen `cctv-data`; conserva ese volumen al actualizar.
- No publiques el puerto 8080 directamente en Internet. Para acceso remoto, coloca el panel detrás de una VPN o un proxy inverso con HTTPS.
- La reproducción usa WebRTC (menos de 1 s de retraso) y cae a HLS si WebRTC no conecta. H.264 es el códec más compatible; H.265 solo funciona en navegadores con decodificación por hardware.
- En algunos equipos, la búsqueda multicast está bloqueada por VLAN, Wi-Fi de invitados o firewall; en ese caso añade la IP manualmente.

## Grabación

Cada cámara se graba de forma continua en `/mnt/cctv/recordings` (segmentos fMP4 de 10 minutos, sin recodificar). El panel borra los segmentos más antiguos cuando el total supera `RECORDINGS_MAX_GB` (500 GB por defecto; ambos ajustables en `.env` con `RECORDINGS_MAX_GB` y `RECORDINGS_HOST_DIR`). Los eventos de movimiento ONVIF se guardan en la base de datos y se marcan en la línea de tiempo de la pestaña **Grabaciones**.

La carpeta debe pertenecer al usuario de los contenedores (UID 10001):

```sh
mkdir -p /mnt/cctv/recordings
docker run --rm --user 0 --entrypoint "" -v /mnt/cctv/recordings:/r nvr-cctv-cctv chown 10001:10001 /r
```

## Mantenimiento

```sh
docker compose logs -f cctv mediamtx
docker compose pull mediamtx
docker compose up -d --build
```

## Diagnóstico de reproducción

Mientras el vídeo está abierto en el panel, consulta el endpoint autenticado
`/api/cameras/ID_DE_CAMARA/diagnostics`. Informa del estado de la ruta en
MediaMTX, las directivas LL-HLS, el códec/FPS anunciado por el perfil ONVIF y
los últimos 50 eventos del navegador (incluidos errores HLS fatales y no
fatales). No devuelve la URL RTSP ni credenciales. Úsalo para diferenciar una
fuente sin pistas o sin partes HLS de un fallo de decodificación en el navegador.

## Estructura del código

- `app/main.py`: ensambla la aplicación.
- `app/config.py`: rutas, variables de entorno y límites.
- `app/db.py`: SQLite y cifrado de credenciales de cámaras.
- `app/security.py`: contraseñas (scrypt), sesiones y middleware de acceso.
- `app/onvif_service.py`: perfiles, sesiones ONVIF cacheadas, PTZ y descubrimiento.
- `app/mediamtx.py`: cliente de la API, HLS, WebRTC y reproducción de MediaMTX.
- `app/recorder.py`: mantiene la grabación activa, aplica el límite de espacio y registra el movimiento.
- `app/routers/`: endpoints HTTP (`pages`, `auth`, `cameras`, `controls`, `streaming`, `recordings`).
- `app/static/js/`: módulos del navegador (`main`, `live`, `player`, `audio`, `enhance`, `recordings`, `cameras`, `system`, `account`, `api`, `ui`).
