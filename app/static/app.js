const grid = document.querySelector('#camera-grid');
const emptyState = document.querySelector('#empty-state');
const count = document.querySelector('#camera-count');
const gridStatus = document.querySelector('#grid-status');
const cameraDialog = document.querySelector('#camera-modal');
const discoveryDialog = document.querySelector('#discover-modal');
const cameraForm = document.querySelector('#camera-form');
const profileSelect = cameraForm.elements.profile_token;
const toast = document.querySelector('#toast');
const players = new Map();
let toastTimer;

function notify(message) {
  toast.textContent = message;
  toast.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    if (typeof detail === 'string') throw new Error(detail);
    if (Array.isArray(detail)) {
      const labels = {
        name: 'Nombre del canal',
        host: 'Dirección IP',
        port: 'Puerto ONVIF',
        username: 'Usuario',
        password: 'Contraseña',
        profile_token: 'Perfil de vídeo',
      };
      const messages = detail.map((item) => {
        const field = labels[item.loc?.at(-1)];
        const message = item.msg === 'Field required' ? 'es obligatorio' : item.msg;
        return field ? `${field}: ${message}` : message;
      });
      throw new Error(messages.join('. '));
    }
    throw new Error(detail?.msg || `Error ${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function makeIconButton(label, icon, action, className = '') {
  const button = document.createElement('button');
  button.className = `icon-button ${className}`.trim();
  button.type = 'button';
  button.title = label;
  button.setAttribute('aria-label', label);
  button.textContent = icon;
  button.addEventListener('click', action);
  return button;
}

function reportPlaybackEvent(camera, video, event, detail = '', fatal = false, player = null) {
  const latency = Number.isFinite(player?.latency) ? player.latency : null;
  void api(`/api/cameras/${camera.id}/playback-events`, {
    method: 'POST',
    body: JSON.stringify({
      event,
      detail: String(detail).slice(0, 120),
      fatal,
      media_time: Number.isFinite(video.currentTime) ? video.currentTime : null,
      latency,
    }),
  }).catch(() => {});
}

function destroyPlayer(cameraId) {
  const player = players.get(cameraId);
  if (player && !player.native) player.destroy();
  players.delete(cameraId);
}

async function restartPlayer(camera, video, overlay, tag, button) {
  destroyPlayer(camera.id);
  video.removeAttribute('src');
  video.load();
  reportPlaybackEvent(camera, video, 'manual-restart');
  await startPlayer(camera, video, overlay, tag, button);
}

function createCameraCard(camera, index) {
  const card = document.createElement('article');
  card.className = 'camera-card';

  const feed = document.createElement('div');
  feed.className = 'feed';
  const video = document.createElement('video');
  video.muted = true;
  video.autoplay = true;
  video.preload = 'auto';
  video.playsInline = true;
  video.setAttribute('aria-label', `Vídeo de ${camera.name}`);
  video.addEventListener('playing', () => reportPlaybackEvent(camera, video, 'playing'));
  video.addEventListener('waiting', () => reportPlaybackEvent(camera, video, 'waiting'));
  video.addEventListener('stalled', () => reportPlaybackEvent(camera, video, 'stalled'));
  const overlay = document.createElement('div');
  overlay.className = 'feed-overlay';
  overlay.innerHTML = '<span>◉</span><span>PULSA REPRODUCIR</span>';
  const tag = document.createElement('div');
  tag.className = 'feed-tag';
  tag.innerHTML = '<span class="state-dot"></span><span>ESPERA</span>';
  const time = document.createElement('span');
  time.className = 'feed-time';
  time.textContent = new Date().toLocaleTimeString('es-ES', { hour12: false });
  const videoControls = document.createElement('div');
  videoControls.className = 'video-controls';
  const play = makeIconButton('Pausar vídeo', 'Ⅱ', () => {
    if (video.paused) video.play().catch(() => startPlayer(camera, video, overlay, tag, play));
    else video.pause();
  }, 'video-control');
  video.addEventListener('play', () => { play.textContent = 'Ⅱ'; play.title = 'Pausar vídeo'; });
  video.addEventListener('pause', () => { play.textContent = '▶'; play.title = 'Reanudar vídeo'; });
  const mute = makeIconButton('Activar sonido', '◖', () => {
    video.muted = !video.muted;
    mute.textContent = video.muted ? '◖' : '◗';
    mute.title = video.muted ? 'Activar sonido' : 'Silenciar';
  }, 'video-control');
  const fullscreen = makeIconButton('Pantalla completa', '⛶', () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else feed.requestFullscreen?.();
  }, 'video-control');
  const restart = makeIconButton('Volver al directo', '↻', () => {
    restartPlayer(camera, video, overlay, tag, play);
  }, 'video-control');
  videoControls.append(play, restart, mute, fullscreen);
  feed.append(video, overlay, tag, time, videoControls);

  const info = document.createElement('div');
  info.className = 'camera-info';
  const number = document.createElement('span');
  number.className = 'camera-index';
  number.textContent = String(index + 1).padStart(2, '0');
  const details = document.createElement('div');
  details.className = 'camera-details';
  const name = document.createElement('strong');
  name.textContent = camera.name;
  const meta = document.createElement('span');
  meta.className = 'camera-meta';
  meta.textContent = `${camera.host}:${camera.port}  /  ONVIF`;
  details.append(name, meta);
  const controls = document.createElement('div');
  controls.className = 'camera-controls';
  const ptzToggle = makeIconButton('Controles de cámara', '⌖', () => {
    toolbox.hidden = !toolbox.hidden;
    ptzToggle.classList.toggle('is-playing', !toolbox.hidden);
    if (!toolbox.dataset.loaded) loadCameraControls(camera, toolbox);
  });
  const remove = makeIconButton('Eliminar cámara', '×', () => removeCamera(camera, card));
  controls.append(ptzToggle, remove);
  info.append(number, details, controls);
  const toolbox = createCameraToolbox(camera);
  card.append(feed, info, toolbox);
  setTimeout(() => startPlayer(camera, video, overlay, tag, play), 0);
  return card;
}

function createCameraToolbox(camera) {
  const toolbox = document.createElement('div');
  toolbox.className = 'camera-toolbox';
  toolbox.hidden = true;
  toolbox.dataset.loaded = '';
  toolbox.innerHTML = `
    <div class="toolbox-heading"><span>CONTROLES ONVIF</span><span class="ptz-status">CONSULTANDO</span></div>
    <div class="toolbox-content">
      <div class="ptz-pad" aria-label="Movimiento PTZ">
        <button type="button" data-pan="-1" data-tilt="1" aria-label="Arriba a la izquierda">↖</button>
        <button type="button" data-pan="0" data-tilt="1" aria-label="Arriba">↑</button>
        <button type="button" data-pan="1" data-tilt="1" aria-label="Arriba a la derecha">↗</button>
        <button type="button" data-pan="-1" data-tilt="0" aria-label="Izquierda">←</button>
        <button type="button" data-stop aria-label="Detener movimiento">·</button>
        <button type="button" data-pan="1" data-tilt="0" aria-label="Derecha">→</button>
        <button type="button" data-pan="-1" data-tilt="-1" aria-label="Abajo a la izquierda">↙</button>
        <button type="button" data-pan="0" data-tilt="-1" aria-label="Abajo">↓</button>
        <button type="button" data-pan="1" data-tilt="-1" aria-label="Abajo a la derecha">↘</button>
      </div>
      <div class="camera-options">
        <div class="profile-control"><label>Perfil de vídeo<select data-profile disabled><option>Consultando…</option></select></label><button type="button" data-apply-profile disabled>Aplicar</button></div>
        <label class="ir-control">Filtro día/noche<select data-ir disabled><option>Consultando…</option></select></label>
        <div class="zoom-control"><span>ZOOM</span><div><button type="button" data-zoom="1" aria-label="Acercar">＋</button><button type="button" data-zoom="-1" aria-label="Alejar">−</button></div></div>
        <p class="light-note" data-light-note></p>
      </div>
    </div>`;
  toolbox.dataset.camera = camera.id;
  return toolbox;
}

async function loadCameraControls(camera, toolbox) {
  const status = toolbox.querySelector('.ptz-status');
  const pad = toolbox.querySelector('.ptz-pad');
  const zoom = toolbox.querySelector('.zoom-control');
  const selector = toolbox.querySelector('[data-ir]');
  const profileSelect = toolbox.querySelector('[data-profile]');
  const applyProfile = toolbox.querySelector('[data-apply-profile]');
  const lightNote = toolbox.querySelector('[data-light-note]');
  toolbox.dataset.loaded = 'loading';
  try {
    const [capabilities, profileData] = await Promise.all([
      api(`/api/cameras/${camera.id}/controls`),
      api(`/api/cameras/${camera.id}/profiles`),
    ]);
    status.textContent = capabilities.ptz ? 'PTZ DISPONIBLE' : 'SIN PTZ';
    pad.hidden = !capabilities.ptz;
    zoom.hidden = !capabilities.zoom;
    selector.replaceChildren(new Option('No disponible', ''));
    for (const mode of capabilities.ir_cut_modes) {
      selector.add(new Option({
        AUTO: 'Automático',
        ON: 'Día (filtro activado)',
        OFF: 'Noche (filtro retirado)',
      }[mode] || mode, mode));
    }
    selector.disabled = capabilities.ir_cut_modes.length === 0;
    selector.value = capabilities.ir_cut_mode || capabilities.ir_cut_modes[0] || '';
    lightNote.textContent = capabilities.white_light
      ? 'Luz auxiliar disponible en la cámara.'
      : 'La cámara no expone control ONVIF de luz blanca.';
    profileSelect.replaceChildren(...profileData.profiles.map((profile) => {
      const format = profile.width && profile.height ? `${profile.width}×${profile.height}` : 'Resolución desconocida';
      const codec = profile.encoding || 'Códec desconocido';
      const fps = profile.frame_rate ? ` · ${profile.frame_rate} FPS` : '';
      return new Option(`${profile.name} · ${format} · ${codec}${fps}`, profile.token, false, profile.selected);
    }));
    profileSelect.disabled = false;
    applyProfile.disabled = true;
    profileSelect.addEventListener('change', () => { applyProfile.disabled = false; });
    applyProfile.addEventListener('click', async () => {
      applyProfile.disabled = true;
      profileSelect.disabled = true;
      try {
        await api(`/api/cameras/${camera.id}/profile`, {
          method: 'PUT',
          body: JSON.stringify({ profile_token: profileSelect.value }),
        });
        notify('Perfil aplicado. Reconectando al directo…');
        await refreshCameras();
      } catch (error) {
        notify(error.message);
      } finally {
        profileSelect.disabled = false;
      }
    });
    selector.addEventListener('change', async () => {
      selector.disabled = true;
      try {
        await api(`/api/cameras/${camera.id}/ir-cut`, {
          method: 'PUT',
          body: JSON.stringify({ mode: selector.value }),
        });
        notify(`Filtro día/noche: ${selector.selectedOptions[0].textContent}`);
      } catch (error) {
        notify(error.message);
      } finally {
        selector.disabled = false;
      }
    });
    pad.querySelectorAll('button').forEach((button) => {
      button.addEventListener('pointerdown', (event) => {
        event.preventDefault();
        if (button.hasAttribute('data-stop')) {
          stopPTZ(camera.id);
          return;
        }
        button.setPointerCapture?.(event.pointerId);
        movePTZ(camera.id, {
          pan: Number(button.dataset.pan),
          tilt: Number(button.dataset.tilt),
          duration_ms: 1800,
        });
      });
      button.addEventListener('pointerup', () => stopPTZ(camera.id));
      button.addEventListener('pointercancel', () => stopPTZ(camera.id));
    });
    zoom.querySelectorAll('button').forEach((button) => {
      button.addEventListener('pointerdown', (event) => {
        event.preventDefault();
        button.setPointerCapture?.(event.pointerId);
        movePTZ(camera.id, { zoom: Number(button.dataset.zoom), duration_ms: 1800 });
      });
      button.addEventListener('pointerup', () => stopPTZ(camera.id));
      button.addEventListener('pointercancel', () => stopPTZ(camera.id));
    });
    toolbox.dataset.loaded = 'true';
  } catch (error) {
    status.textContent = 'CONTROLES NO DISPONIBLES';
    toolbox.dataset.loaded = '';
    notify(error.message);
  }
}

async function movePTZ(cameraId, movement) {
  try {
    await api(`/api/cameras/${cameraId}/ptz/move`, {
      method: 'POST',
      body: JSON.stringify(movement),
    });
  } catch (error) {
    notify(error.message);
  }
}

async function stopPTZ(cameraId) {
  try {
    await api(`/api/cameras/${cameraId}/ptz/stop`, { method: 'POST' });
  } catch (error) {
    notify(error.message);
  }
}

async function startPlayer(camera, video, overlay, tag, button) {
  if (players.has(camera.id)) {
    await video.play().catch(() => {});
    return;
  }
  button.disabled = true;
  tag.lastElementChild.textContent = 'CONECTANDO';
  overlay.lastElementChild.textContent = 'CONECTANDO CON LA CÁMARA';
  overlay.classList.remove('is-hidden');
  try {
    const result = await api(`/api/cameras/${camera.id}/stream`, { method: 'POST' });
    const playlist = result.playlist;
    video.addEventListener('playing', () => {
      tag.classList.add('is-live');
      tag.lastElementChild.textContent = 'EN DIRECTO';
      overlay.classList.add('is-hidden');
      button.classList.add('is-playing');
      button.title = 'Reiniciar reproducción';
    }, { once: true });
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = playlist;
      players.set(camera.id, { native: true });
    } else if (window.Hls?.isSupported()) {
      const previous = players.get(camera.id);
      previous?.destroy();
      const player = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
        liveSyncDuration: 0.8,
        liveMaxLatencyDuration: 2.5,
        maxLiveSyncPlaybackRate: 1.35,
        maxBufferLength: 3,
        maxMaxBufferLength: 5,
        backBufferLength: 0,
      });
      const recoveryAttempts = { network: 0, media: 0 };
      players.set(camera.id, player);
      player.loadSource(playlist);
      player.attachMedia(video);
      player.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
      player.on(Hls.Events.FRAG_BUFFERED, () => {
        recoveryAttempts.network = 0;
        recoveryAttempts.media = 0;
      });
      player.on(Hls.Events.ERROR, (_event, data) => {
        reportPlaybackEvent(camera, video, data.type || 'hls-error', data.details || '', Boolean(data.fatal), player);
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR && recoveryAttempts.network++ < 2) {
          player.startLoad();
          return;
        }
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR && recoveryAttempts.media++ < 2) {
          player.recoverMediaError();
          return;
        }
        let message = `No se pudo abrir el flujo de ${camera.name}`;
        try {
          const body = JSON.parse(data.response?.text || '{}');
          if (body.detail) message = body.detail;
        } catch {
          message = `No se pudo abrir el flujo de ${camera.name}`;
        }
        overlay.lastElementChild.textContent = message;
        overlay.classList.remove('is-hidden');
        tag.classList.remove('is-live');
        tag.lastElementChild.textContent = 'SIN SEÑAL';
        button.classList.remove('is-playing');
        destroyPlayer(camera.id);
        notify(message);
      });
    } else {
      throw new Error('Este navegador no admite reproducción HLS');
    }
    await video.play().catch(() => {});
  } catch (error) {
    tag.classList.remove('is-live');
    tag.lastElementChild.textContent = 'SIN SEÑAL';
    overlay.lastElementChild.textContent = error.message;
    overlay.classList.remove('is-hidden');
    notify(error.message);
  } finally {
    button.disabled = false;
  }
}

async function removeCamera(camera, card) {
  if (!window.confirm(`¿Eliminar el canal “${camera.name}”?`)) return;
  try {
    await api(`/api/cameras/${camera.id}`, { method: 'DELETE' });
    destroyPlayer(camera.id);
    card.remove();
    await refreshCameras();
    notify('Cámara eliminada');
  } catch (error) {
    notify(error.message);
  }
}

async function refreshCameras() {
  try {
    const cameras = await api('/api/cameras');
    for (const cameraId of players.keys()) destroyPlayer(cameraId);
    grid.replaceChildren(...cameras.map(createCameraCard));
    count.textContent = String(cameras.length).padStart(2, '0');
    document.querySelector('#nav-camera-count').textContent = String(cameras.length).padStart(2, '0');
    emptyState.hidden = cameras.length !== 0;
    gridStatus.textContent = cameras.length ? `${String(cameras.length).padStart(2, '0')} CANAL${cameras.length === 1 ? '' : 'ES'} CONFIGURADO${cameras.length === 1 ? '' : 'S'}` : 'ESPERANDO CÁMARAS';
  } catch (error) {
    notify(error.message);
  }
}

function openAdd(host = '', name = '') {
  cameraForm.reset();
  cameraForm.elements.host.value = host;
  cameraForm.elements.name.value = name;
  cameraForm.elements.port.value = '80';
  profileSelect.replaceChildren(new Option('Prueba ONVIF para cargar perfiles', ''));
  profileSelect.disabled = true;
  document.querySelector('#save-camera').disabled = true;
  setFormMessage('');
  cameraDialog.showModal();
}

function setFormMessage(message, error = false) {
  const target = document.querySelector('#form-message');
  target.textContent = message;
  target.classList.toggle('is-error', error);
}

function formPayload() {
  return {
    name: cameraForm.elements.name.value.trim(),
    host: cameraForm.elements.host.value.trim(),
    port: Number(cameraForm.elements.port.value),
    username: cameraForm.elements.username.value,
    password: cameraForm.elements.password.value,
    profile_token: profileSelect.value || null,
  };
}

document.querySelectorAll('[data-close]').forEach((button) => {
  button.addEventListener('click', () => document.getElementById(button.dataset.close).close());
});
document.querySelector('#add-open').addEventListener('click', () => openAdd());
document.querySelector('#empty-add').addEventListener('click', () => openAdd());
document.querySelector('#discover-open').addEventListener('click', scanDevices);
document.querySelector('#discover-open-top').addEventListener('click', scanDevices);
document.querySelector('#empty-discover').addEventListener('click', scanDevices);
document.querySelector('#rail-cameras').addEventListener('click', () => document.querySelector('.camera-section').scrollIntoView({ behavior: 'smooth' }));

document.querySelector('#test-camera').addEventListener('click', async (event) => {
  if (!cameraForm.reportValidity()) return;
  const button = event.currentTarget;
  button.disabled = true;
  setFormMessage('Consultando perfiles ONVIF…');
  try {
    const result = await api('/api/cameras/test', { method: 'POST', body: JSON.stringify(formPayload()) });
    const recommended = result.profiles.find((profile) => profile.recommended) || result.profiles[0];
    profileSelect.replaceChildren(...result.profiles.map((profile) => {
      const resolution = profile.width && profile.height ? ` · ${profile.width}x${profile.height}` : '';
      const codec = profile.encoding ? ` · ${profile.encoding}` : '';
      const fps = profile.frame_rate ? ` · ${profile.frame_rate} FPS` : '';
      const badge = profile.recommended ? ' · recomendado' : '';
      return new Option(`${profile.name}${resolution}${codec}${fps}${badge}`, profile.token);
    }));
    profileSelect.value = recommended.token;
    profileSelect.disabled = false;
    document.querySelector('#save-camera').disabled = false;
    const resolution = recommended.width && recommended.height
      ? ` (${recommended.width}x${recommended.height})`
      : '';
    setFormMessage(`${result.profiles.length} perfil${result.profiles.length === 1 ? '' : 'es'} encontrado${result.profiles.length === 1 ? '' : 's'}. Seleccionado: ${recommended.name}${resolution}.`);
  } catch (error) {
    profileSelect.disabled = true;
    document.querySelector('#save-camera').disabled = true;
    setFormMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
});

cameraForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const save = document.querySelector('#save-camera');
  save.disabled = true;
  setFormMessage('Guardando cámara…');
  try {
    await api('/api/cameras', { method: 'POST', body: JSON.stringify(formPayload()) });
    cameraDialog.close();
    await refreshCameras();
    notify('Cámara añadida');
  } catch (error) {
    setFormMessage(error.message, true);
  } finally {
    save.disabled = false;
  }
});

async function scanDevices() {
  discoveryDialog.showModal();
  const status = document.querySelector('#discovery-status');
  const list = document.querySelector('#discovery-list');
  const scan = document.querySelector('#scan-again');
  status.innerHTML = '<span class="scan-indicator"></span>Buscando dispositivos en la red local…';
  list.replaceChildren();
  scan.disabled = true;
  try {
    const devices = await api('/api/cameras/discover', { method: 'POST' });
    status.textContent = devices.length ? `${devices.length} dispositivo${devices.length === 1 ? '' : 's'} encontrado${devices.length === 1 ? '' : 's'}` : 'No se encontraron cámaras ONVIF';
    for (const device of devices) {
      const row = document.createElement('div');
      row.className = 'discovery-device';
      const mark = document.createElement('span');
      mark.className = 'device-mark';
      mark.textContent = 'IP';
      const details = document.createElement('div');
      details.className = 'device-details';
      const name = document.createElement('strong');
      name.textContent = device.name;
      const address = document.createElement('span');
      address.textContent = `${device.host}:${device.port}`;
      details.append(name, address);
      const add = document.createElement('button');
      add.type = 'button';
      add.className = 'button button-quiet device-add';
      add.textContent = 'Configurar';
      add.addEventListener('click', () => {
        discoveryDialog.close();
        openAdd(device.host, device.name);
        cameraForm.elements.port.value = String(device.port);
      });
      row.append(mark, details, add);
      list.append(row);
    }
  } catch (error) {
    status.textContent = error.message;
  } finally {
    scan.disabled = false;
  }
}

document.querySelector('#scan-again').addEventListener('click', scanDevices);
function updateClock() {
  document.querySelector('#clock').textContent = new Date().toLocaleTimeString('es-ES', { hour12: false });
}
setInterval(updateClock, 1000);
updateClock();
refreshCameras();
