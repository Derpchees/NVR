# NVR CCTV

Panel web local para descubrir cámaras ONVIF, validar perfiles y ver flujos RTSP en el navegador mediante HLS.

## Requisitos

- Docker Engine y Docker Compose en un host Linux.
- Cámaras y host en una red local que permita WS-Discovery multicast UDP/3702.
- Puerto TCP 8080 accesible desde los equipos que usarán el panel.

El modo `network_mode: host` se utiliza para que el contenedor reciba WS-Discovery y alcance las cámaras en la red del host. El panel escucha en 8080; la API y HLS de MediaMTX solo escuchan en loopback.

## Inicio

```sh
cp .env.example .env
```

Edita `.env` y establece una contraseña única para el panel. Establece también `HLS_CDN_SECRET` con un secreto aleatorio largo; el mismo secreto se comparte internamente entre el panel y MediaMTX para autorizar playlists y segmentos, sin exponer la API de vídeo al navegador. Después:

```sh
docker compose up -d --build
```

Abre `http://IP_DEL_SERVIDOR:8080` e inicia sesión con los valores de `.env`. Usa **Buscar ONVIF** o **Añadir cámara**. La cámara necesita ONVIF activado y una cuenta con acceso a sus perfiles de vídeo.

## Notas

- Las contraseñas ONVIF se cifran en la base de datos persistente de la aplicación. La clave de cifrado también queda en el volumen `cctv-data`; conserva ese volumen al actualizar.
- No publiques el puerto 8080 directamente en Internet. Para acceso remoto, coloca el panel detrás de una VPN o un proxy inverso con HTTPS.
- HLS añade unos segundos de latencia. El códec de vídeo de la cámara debe ser compatible con el navegador (H.264 es la opción más compatible).
- En algunos equipos, la búsqueda multicast está bloqueada por VLAN, Wi-Fi de invitados o firewall; en ese caso añade la IP manualmente.

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
