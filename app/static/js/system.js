// "Sistema" tab: per-camera stream diagnostics.

import { api } from './api.js';
import { playbackMode } from './live.js';
import { el } from './ui.js';

const container = document.querySelector('#diagnostics');
const refreshButton = document.querySelector('#diagnostics-refresh');
let cameras = [];

const BAD_EVENTS = new Set(['stalled', 'lost', 'webrtc-fallback', 'networkError', 'mediaError']);

export function init() {
  refreshButton.addEventListener('click', refresh);
}

export function setCameras(list) {
  cameras = list;
}

function row(label, value) {
  return el('div', {}, el('dt', { text: label }), el('dd', { text: value ?? '—' }));
}

function card(camera, data) {
  const video = data.mediamtx.track_details?.find((track) => !['G711', 'Opus', 'MPEG-4 Audio'].includes(track.codec));
  const onvifCodec = data.profile.encoding;
  const mismatch = video && onvifCodec && !video.codec.startsWith(onvifCodec.replace('.', ''));
  const mode = playbackMode(camera.id);
  const events = data.browser_events.slice(-12).reverse();
  return el('section', { class: 'panel diag-card' },
    el('div', { class: 'panel-head' },
      el('div', {}, el('span', { class: 'eyebrow', text: `${camera.host}:${camera.port}` }), el('h2', { text: camera.name })),
      el('span', {
        class: `badge ${data.mediamtx.ready ? 'badge-live' : 'badge-error'}`,
        text: data.mediamtx.ready ? 'Recibiendo vídeo' : 'Sin vídeo',
      }),
    ),
    el('dl', { class: 'info-list' },
      row('Códec real', video ? `${video.codec} · ${video.width}×${video.height}` : null),
      row('Códec según ONVIF', onvifCodec ? `${onvifCodec}${mismatch ? ' (no coincide)' : ''}` : null),
      row('FPS configurados', data.profile.frame_rate_limit),
      row('Transmisión en este navegador', mode === 'HLS' ? 'HLS (respaldo, con retraso)' : mode),
      row('Espectadores', `${data.mediamtx.readers ?? 0} (${data.mediamtx.webrtc_readers ?? 0} WebRTC)`),
      row('Cuadros con error', data.mediamtx.inbound_frames_in_error),
    ),
    events.length
      ? el('ul', { class: 'event-log' }, ...events.map((event) => el('li', {
        class: BAD_EVENTS.has(event.event) || event.fatal ? 'is-bad' : '',
        text: `${event.recorded_at.slice(11, 19)}  ${event.event}${event.detail ? ` · ${event.detail}` : ''}`,
      })))
      : el('p', { class: 'hint', text: 'Sin eventos del navegador todavía.' }),
  );
}

export async function refresh() {
  if (!cameras.length) {
    container.replaceChildren(el('p', { class: 'muted', text: 'No hay cámaras configuradas.' }));
    return;
  }
  refreshButton.disabled = true;
  container.replaceChildren(el('div', { class: 'spinner' }));
  const cards = await Promise.all(cameras.map((camera) => api(`/api/cameras/${camera.id}/diagnostics`)
    .then((data) => card(camera, data))
    .catch((error) => el('section', { class: 'panel diag-card' }, el('h2', { text: camera.name }), el('p', { class: 'muted', text: error.message })))));
  container.replaceChildren(...cards);
  refreshButton.disabled = false;
}
