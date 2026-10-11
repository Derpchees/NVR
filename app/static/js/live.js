// "En vivo" tab: video tiles plus the control panel for the selected camera.

import { api } from './api.js';
import { createAudio } from './audio.js';
import { createEnhancer } from './enhance.js';
import { play, reportEvent } from './player.js';
import { el, icon, iconButton, notify, timeNow } from './ui.js';

const grid = document.querySelector('#live-grid');
const layout = document.querySelector('#live-layout');
const empty = document.querySelector('#live-empty');
const controlTitle = document.querySelector('#control-title');
const controlResolution = document.querySelector('#control-resolution');
const controlBody = document.querySelector('#control-body');

const tiles = new Map(); // cameraId -> { camera, root, video, session, ... }
const capabilities = new Map(); // cameraId -> Promise of /controls
const ptzQueues = new Map(); // cameraId -> Promise chain keeping move/stop in order
let selectedId = null;

const PTZ_KEEPALIVE_MS = 1200;
const PTZ_MOVE_MS = 1800;

export function playbackMode(cameraId) {
  return tiles.get(cameraId)?.session?.mode || null;
}

export function render(cameras) {
  for (const [id, tile] of tiles) {
    if (!cameras.some((camera) => camera.id === id)) {
      tile.session?.destroy();
      tile.audio?.destroy();
      tiles.delete(id);
    }
  }
  empty.hidden = cameras.length > 0;
  layout.hidden = cameras.length === 0;
  grid.replaceChildren(...cameras.map((camera) => (tiles.get(camera.id) || createTile(camera)).root));
  if (!cameras.some((camera) => camera.id === selectedId)) select(cameras[0]?.id || null);
}

let tickCount = 0;

export function tick() {
  const now = timeNow();
  for (const tile of tiles.values()) tile.time.textContent = now;
  if (tickCount++ % 2 === 0) for (const tile of tiles.values()) updateStats(tile);
}

async function updateStats(tile) {
  const session = tile.session;
  if (!session?.stats) return;
  const stats = await session.stats().catch(() => null);
  if (!stats || tile.session !== session) return;
  const parts = [session.mode];
  if (stats.bufferMs != null) parts.push(`${stats.bufferMs} ms`);
  if (stats.fps) parts.push(`${Math.round(stats.fps)} fps`);
  tile.mode.textContent = parts.join(' · ');
  tile.mode.title = 'Retraso añadido por el navegador y cuadros por segundo recibidos';
}

function enhancePreference(cameraId, value) {
  const key = `nvr-enhance-${cameraId}`;
  try {
    if (value === undefined) return localStorage.getItem(key) === '1';
    localStorage.setItem(key, value ? '1' : '0');
  } catch {
    return false;
  }
  return value;
}

function createTile(camera) {
  const video = el('video', { muted: true, autoplay: true, playsinline: true, 'aria-label': `Vídeo de ${camera.name}` });
  video.muted = true;
  const state = el('span', { class: 'badge', text: 'Conectando' });
  const mode = el('span', { class: 'badge' });
  const time = el('span', { class: 'tile-time', text: timeNow() });
  const message = el('p', { text: 'Conectando con la cámara…' });
  const overlay = el('div', { class: 'tile-overlay is-loading' }, el('div', { class: 'spinner' }), message);
  const pause = iconButton('Pausar', 'pause', (event) => {
    event.stopPropagation();
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  });
  // The video element stays muted; sound plays through audio.js.
  const sound = iconButton('Activar sonido', 'muted', (event) => {
    event.stopPropagation();
    tile.audio ||= video.srcObject ? createAudio(video.srcObject) : null;
    if (!tile.audio) return;
    tile.soundOn = !tile.soundOn;
    tile.audio.setEnabled(tile.soundOn);
    sound.replaceChildren(icon(tile.soundOn ? 'sound' : 'muted'));
    sound.title = tile.soundOn ? 'Silenciar' : 'Activar sonido';
    sound.setAttribute('aria-label', sound.title);
  });
  sound.hidden = true;
  const enhance = iconButton('Mejorar imagen', 'sparkle', (event) => {
    event.stopPropagation();
    setEnhanced(tile, !tile.enhancer?.enabled);
  });
  const root = el('article', { class: 'tile', tabindex: '0', 'aria-label': camera.name },
    video,
    el('div', { class: 'tile-top' }, el('span', { class: 'tile-name', text: camera.name }), el('div', { class: 'tile-badges' }, mode, state)),
    overlay,
    el('div', { class: 'tile-bottom' }, time, el('div', { class: 'tile-actions' },
      pause,
      sound,
      enhance,
      iconButton('Reconectar', 'reload', (event) => { event.stopPropagation(); connect(tile); }),
      iconButton('Pantalla completa', 'expand', (event) => {
        event.stopPropagation();
        if (document.fullscreenElement) document.exitFullscreen();
        else root.requestFullscreen?.();
      }),
    )),
  );
  const tile = { camera, root, video, state, mode, time, overlay, message, sound, enhance, session: null, audio: null, soundOn: false, enhancer: null };
  if (enhancePreference(camera.id)) video.addEventListener('loadeddata', () => setEnhanced(tile, true), { once: true });
  root.addEventListener('click', () => select(camera.id));
  root.addEventListener('keydown', (event) => { if (event.key === 'Enter') select(camera.id); });
  root.addEventListener('dblclick', () => (document.fullscreenElement ? document.exitFullscreen() : root.requestFullscreen?.()));
  video.addEventListener('playing', () => {
    setState(tile, 'live');
    reportEvent(camera.id, video, 'playing');
  });
  video.addEventListener('waiting', () => reportEvent(camera.id, video, 'waiting'));
  video.addEventListener('play', () => pause.replaceChildren(icon('pause')));
  video.addEventListener('pause', () => pause.replaceChildren(icon('play')));
  tiles.set(camera.id, tile);
  connect(tile);
  return tile;
}

function setEnhanced(tile, value) {
  if (value && !tile.enhancer) {
    try {
      tile.enhancer = createEnhancer(tile.video, tile.root);
    } catch {
      tile.enhancer = null;
    }
    if (!tile.enhancer) {
      notify('Este navegador no admite la mejora de imagen (WebGL2)');
      return;
    }
  }
  tile.enhancer?.setEnabled(value);
  tile.enhance.classList.toggle('is-active', value);
  tile.enhance.title = value ? 'Imagen original' : 'Mejorar imagen';
  enhancePreference(tile.camera.id, value);
}

function setState(tile, state, text = '') {
  tile.state.className = `badge ${state === 'live' ? 'badge-live' : state === 'error' ? 'badge-error' : ''}`;
  tile.state.textContent = { live: 'En vivo', loading: 'Conectando', error: 'Sin señal' }[state];
  tile.overlay.classList.toggle('is-hidden', state === 'live');
  tile.overlay.classList.toggle('is-loading', state === 'loading');
  if (text) tile.message.textContent = text;
}

async function connect(tile) {
  tile.session?.destroy();
  tile.session = null;
  tile.mode.textContent = '';
  tile.audio?.destroy();
  tile.audio = null;
  tile.soundOn = false;
  tile.sound.hidden = true;
  tile.sound.replaceChildren(icon('muted'));
  setState(tile, 'loading', 'Conectando con la cámara…');
  try {
    tile.session = await play(tile.camera.id, tile.video, (reason) => {
      reportEvent(tile.camera.id, tile.video, 'lost', reason, true);
      tile.session?.destroy();
      tile.session = null;
      setState(tile, 'error', `${reason}. Pulsa reconectar.`);
    });
    tile.mode.textContent = tile.session.mode;
    // Browsers only start audio after a click, so sound is opt-in per tile.
    tile.sound.hidden = !tile.session.audio;
  } catch (error) {
    setState(tile, 'error', error.message);
  }
}

// ---- Control panel ----

function select(cameraId) {
  selectedId = cameraId;
  for (const [id, tile] of tiles) tile.root.classList.toggle('is-selected', id === cameraId);
  const tile = tiles.get(cameraId);
  controlResolution.textContent = '';
  if (!tile) {
    controlTitle.textContent = 'Selecciona una cámara';
    controlBody.replaceChildren(el('p', { class: 'muted', text: 'Pulsa sobre una cámara para controlarla.' }));
    return;
  }
  controlTitle.textContent = tile.camera.name;
  controlBody.replaceChildren(el('div', { class: 'spinner' }));
  if (!capabilities.has(cameraId)) {
    capabilities.set(cameraId, api(`/api/cameras/${cameraId}/controls`).catch((error) => {
      capabilities.delete(cameraId);
      throw error;
    }));
  }
  capabilities.get(cameraId).then((caps) => {
    if (selectedId === cameraId) renderControls(tile.camera, caps);
  }).catch((error) => {
    if (selectedId !== cameraId) return;
    controlBody.replaceChildren(
      el('p', { class: 'muted', text: error.message }),
      el('button', { class: 'button', type: 'button', text: 'Reintentar', onclick: () => select(cameraId) }),
    );
  });
}

function sendPTZ(cameraId, action, body) {
  // Chain requests so a release (stop) can never reach the camera before its move.
  const previous = ptzQueues.get(cameraId) || Promise.resolve();
  const next = previous.then(() => api(`/api/cameras/${cameraId}/ptz/${action}`, { method: 'POST', body }))
    .catch((error) => notify(error.message));
  ptzQueues.set(cameraId, next);
  return next;
}

function holdToMove(button, cameraId, movement) {
  let keepalive = null;
  const start = (event) => {
    event.preventDefault();
    button.setPointerCapture?.(event.pointerId);
    button.classList.add('is-pressed');
    tiles.get(cameraId)?.audio?.duck(true);
    sendPTZ(cameraId, 'move', { ...movement, duration_ms: PTZ_MOVE_MS });
    // Re-send while held so long presses keep moving; the camera stops by itself
    // if the browser vanishes mid-press.
    keepalive = setInterval(() => sendPTZ(cameraId, 'move', { ...movement, duration_ms: PTZ_MOVE_MS }), PTZ_KEEPALIVE_MS);
  };
  const end = () => {
    if (!keepalive) return;
    clearInterval(keepalive);
    keepalive = null;
    button.classList.remove('is-pressed');
    tiles.get(cameraId)?.audio?.duck(false);
    sendPTZ(cameraId, 'stop');
  };
  button.addEventListener('pointerdown', start);
  button.addEventListener('pointerup', end);
  button.addEventListener('pointercancel', end);
  button.addEventListener('lostpointercapture', end);
}

const PAD = [
  ['up-left', -1, 1], ['up', 0, 1], ['up-right', 1, 1],
  ['left', -1, 0], null, ['right', 1, 0],
  ['down-left', -1, -1], ['down', 0, -1], ['down-right', 1, -1],
];

const IR_LABELS = { AUTO: 'Auto', ON: 'Día', OFF: 'Noche' };

function infoRow(label, value) {
  return el('div', {}, el('dt', { text: label }), el('dd', { text: value }));
}

// ONVIF auxiliary commands look like "tt:IRLamp|On" or "tt:Light|Off".
function auxLabel(command) {
  const [name, state] = command.replace(/^tt:/, '').split('|');
  const thing = /ir/i.test(name) ? 'Luz IR' : 'Luz';
  return state ? `${thing}: ${/on/i.test(state) ? 'encender' : 'apagar'}` : name;
}

function relaySection(camera, relay, index) {
  // A relay's purpose depends on the model and wiring (white light, siren,
  // door...); ONVIF does not say which, so the label stays generic.
  let active = false;
  const toggle = el('button', { class: 'button', type: 'button', text: 'Activar' });
  toggle.addEventListener('click', async () => {
    toggle.disabled = true;
    try {
      await api(`/api/cameras/${camera.id}/relays/${encodeURIComponent(relay.token)}`, {
        method: 'PUT',
        body: { active: !active },
      });
      active = !active;
      toggle.textContent = active ? 'Desactivar' : 'Activar';
      toggle.classList.toggle('button-primary', active);
      notify(`Salida ${index + 1} ${active ? 'activada' : 'desactivada'}`);
    } catch (error) {
      notify(error.message);
    } finally {
      toggle.disabled = false;
    }
  });
  return el('div', { class: 'control-section' },
    el('span', { class: 'eyebrow', text: `Salida auxiliar ${index + 1}` }),
    el('p', { class: 'hint', text: 'La cámara no indica qué controla (puede ser luz blanca, sirena u otro). Pruébala mirando la cámara.' }),
    toggle,
  );
}

function renderControls(camera, caps) {
  controlResolution.textContent = caps.resolution || '';
  const sections = [];

  if (caps.ptz) {
    const pad = el('div', { class: 'ptz-pad', 'aria-label': 'Movimiento' });
    for (const cell of PAD) {
      if (!cell) {
        pad.append(iconButton('Detener', 'stop', () => sendPTZ(camera.id, 'stop'), 'ptz-center'));
        continue;
      }
      const [name, pan, tilt] = cell;
      const button = el('button', { type: 'button', 'aria-label': name });
      button.append(icon(name));
      holdToMove(button, camera.id, { pan, tilt });
      pad.append(button);
    }
    const zoomIn = el('button', { class: 'button', type: 'button' }, icon('plus'), 'Acercar');
    const zoomOut = el('button', { class: 'button', type: 'button' }, icon('minus'), 'Alejar');
    holdToMove(zoomIn, camera.id, { zoom: 1 });
    holdToMove(zoomOut, camera.id, { zoom: -1 });
    sections.push(
      el('div', { class: 'control-section' }, el('span', { class: 'eyebrow', text: 'Movimiento · mantén pulsado' }), pad),
      el('div', { class: 'control-section' }, el('span', { class: 'eyebrow', text: 'Zoom' }), el('div', { class: 'zoom-row' }, zoomIn, zoomOut)),
    );
  }

  if (caps.ir_cut_modes.length) {
    const order = ['AUTO', 'ON', 'OFF'].filter((mode) => caps.ir_cut_modes.includes(mode));
    const group = el('div', { class: 'segmented', role: 'group', 'aria-label': 'Modo día/noche' });
    const buttons = order.map((mode) => el('button', {
      type: 'button',
      class: mode === caps.ir_cut_mode ? 'is-active' : '',
      text: IR_LABELS[mode],
      onclick: async () => {
        buttons.forEach((button) => { button.disabled = true; });
        try {
          await api(`/api/cameras/${camera.id}/ir-cut`, { method: 'PUT', body: { mode } });
          caps.ir_cut_mode = mode;
          buttons.forEach((button, index) => button.classList.toggle('is-active', order[index] === mode));
          notify(`Modo ${IR_LABELS[mode].toLowerCase()} activado`);
        } catch (error) {
          notify(error.message);
        } finally {
          buttons.forEach((button) => { button.disabled = false; });
        }
      },
    }));
    group.append(...buttons);
    sections.push(el('div', { class: 'control-section' }, el('span', { class: 'eyebrow', text: 'Día / noche' }), group));
  }

  if (caps.light_commands.length) {
    sections.push(el('div', { class: 'control-section' },
      el('span', { class: 'eyebrow', text: 'Luz' }),
      el('div', { class: 'zoom-row' }, ...caps.light_commands.map((command) => el('button', {
        class: 'button',
        type: 'button',
        text: auxLabel(command),
        onclick: () => api(`/api/cameras/${camera.id}/aux`, { method: 'POST', body: { command } })
          .then(() => notify(auxLabel(command)))
          .catch((error) => notify(error.message)),
      }))),
    ));
  }

  for (const [index, relay] of caps.relays.entries()) {
    sections.push(relaySection(camera, relay, index));
  }

  const audioLive = Boolean(tiles.get(camera.id)?.session?.audio);
  const mode = playbackMode(camera.id);
  const device = [caps.device.manufacturer, caps.device.model].filter(Boolean).join(' · ');
  let microphone = 'No';
  if (caps.microphone) {
    microphone = `Sí${caps.audio_codec ? ` · ${caps.audio_codec}` : ''}`;
    if (audioLive) microphone += ' · se oye en el vídeo';
    else if (mode === 'HLS') microphone += ' · no se oye en HLS';
  }
  sections.push(el('div', { class: 'control-section' },
    el('span', { class: 'eyebrow', text: 'Capacidades detectadas' }),
    el('dl', { class: 'info-list' },
      infoRow('Día / noche', caps.ir_cut_modes.length ? 'Sí' : caps.ignored.includes('ir_cut') ? 'Solo desde EasyVMS' : 'No'),
      infoRow('Luz controlable', caps.light_commands.length ? 'Sí' : 'No por ONVIF'),
      infoRow('Micrófono', microphone),
      infoRow('Altavoz (hablar)', caps.speaker ? 'Sí · no integrado aún' : 'No'),
      infoRow('PTZ / zoom', caps.ptz ? `Sí${caps.presets ? ` · ${caps.presets} posiciones` : ''}` : 'No'),
      infoRow('Ajustes de imagen', caps.image.length ? caps.image.join(', ') : 'No'),
    ),
  ));
  sections.push(el('div', { class: 'control-section' },
    el('span', { class: 'eyebrow', text: 'Información' }),
    el('dl', { class: 'info-list' },
      device ? infoRow('Modelo', device) : null,
      caps.device.firmware ? infoRow('Firmware', caps.device.firmware) : null,
      infoRow('Dirección', `${camera.host}:${camera.port}`),
      infoRow('Calidad', caps.resolution ? `${caps.resolution} (máxima)` : '—'),
      infoRow('Transmisión', mode === 'HLS' ? 'HLS · con retraso' : mode || '—'),
    ),
  ));
  for (const note of caps.notes) sections.push(el('p', { class: 'hint', text: note }));
  if (!caps.ptz && !caps.ir_cut_modes.length) {
    sections.unshift(el('p', { class: 'muted', text: 'Esta cámara no ofrece controles ONVIF.' }));
  }
  controlBody.replaceChildren(...sections);
}
