// "Grabaciones" tab: 24 h timeline of recorded spans and motion, playback,
// speed control and clip download.

import { api } from './api.js';
import { el, icon, notify } from './ui.js';

const DAY_MS = 86_400_000;
const MAX_CLIP_S = 4 * 3600;
const SPEEDS = [1, 2, 4, 8];

const cameraSelect = document.querySelector('#rec-camera');
const dayInput = document.querySelector('#rec-day');
const storage = document.querySelector('#rec-storage');
const video = document.querySelector('#rec-video');
const overlay = document.querySelector('#rec-overlay');
const playButton = document.querySelector('#rec-play');
const timeLabel = document.querySelector('#rec-time');
const speedGroup = document.querySelector('#rec-speed');
const clipSelect = document.querySelector('#rec-clip');
const download = document.querySelector('#rec-download');
const timeline = document.querySelector('#rec-timeline');
const track = document.querySelector('#rec-track');
const playhead = document.querySelector('#rec-playhead');
const hours = document.querySelector('#rec-hours');
const eventList = document.querySelector('#rec-events');
const eventsTitle = document.querySelector('#rec-events-title');

let cameras = [];
let spans = []; // [{ start: ms, end: ms }]
let motion = []; // [{ start: ms, end: ms|null }]
let dayStart = 0;
let clip = null; // { start: ms, end: ms } currently loaded in <video>
let speed = 1;
let visible = false;

const pad = (value) => String(value).padStart(2, '0');
const clock = (ms) => {
  const date = new Date(ms);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};
const fileStamp = (ms) => {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`;
};
const localDay = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const gib = (bytes) => `${(bytes / 1024 ** 3).toFixed(bytes < 10 * 1024 ** 3 ? 1 : 0)} GB`;

export function init() {
  dayInput.value = localDay(new Date());
  dayInput.max = dayInput.value;
  for (let hour = 0; hour <= 24; hour += 3) hours.append(el('span', { text: pad(hour) }));
  for (const value of SPEEDS) {
    speedGroup.append(el('button', {
      type: 'button',
      class: value === speed ? 'is-active' : '',
      text: `${value}×`,
      onclick: () => setSpeed(value),
    }));
  }
  playButton.append(icon('play'));
  playButton.addEventListener('click', () => {
    if (!clip) return playAt(spans.at(-1)?.start ?? dayStart);
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  });
  video.addEventListener('play', () => playButton.replaceChildren(icon('pause')));
  video.addEventListener('pause', () => playButton.replaceChildren(icon('play')));
  video.addEventListener('timeupdate', updatePlayhead);
  video.addEventListener('ended', () => clip && playAt(clip.end + 1));
  video.addEventListener('error', () => {
    overlay.lastElementChild.textContent = 'No se pudo reproducir este tramo.';
    overlay.classList.remove('is-hidden');
  });
  cameraSelect.addEventListener('change', load);
  dayInput.addEventListener('change', load);
  document.querySelector('#rec-today').addEventListener('click', () => {
    dayInput.value = localDay(new Date());
    load();
  });
  timeline.addEventListener('click', (event) => {
    const box = track.getBoundingClientRect();
    const ratio = Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1);
    playAt(dayStart + ratio * DAY_MS);
  });
  timeline.addEventListener('keydown', (event) => {
    if (!clip || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    playAt(currentTime() + (event.key === 'ArrowRight' ? 30_000 : -30_000));
  });
  clipSelect.addEventListener('change', updateDownload);
  setInterval(() => {
    if (visible && dayInput.value === localDay(new Date())) load({ keepPlayback: true });
  }, 60_000);
}

export function setCameras(list) {
  const previous = cameraSelect.value;
  cameras = list;
  cameraSelect.replaceChildren(...list.map((camera) => new Option(camera.name, camera.id)));
  if (list.some((camera) => camera.id === previous)) cameraSelect.value = previous;
  if (visible) load();
}

export function show() {
  visible = true;
  load();
}

export function hide() {
  visible = false;
  video.pause();
}

function setSpeed(value) {
  speed = value;
  video.defaultPlaybackRate = value;
  video.playbackRate = value;
  [...speedGroup.children].forEach((button, index) => button.classList.toggle('is-active', SPEEDS[index] === value));
}

async function load({ keepPlayback = false } = {}) {
  const cameraId = cameraSelect.value;
  if (!cameraId) {
    track.replaceChildren();
    eventList.replaceChildren(el('li', { class: 'muted', text: 'No hay cámaras configuradas.' }));
    return;
  }
  const [year, month, day] = dayInput.value.split('-').map(Number);
  dayStart = new Date(year, month - 1, day).getTime();
  const range = `start=${encodeURIComponent(new Date(dayStart).toISOString())}&end=${encodeURIComponent(new Date(dayStart + DAY_MS).toISOString())}`;
  try {
    const [data, status] = await Promise.all([
      api(`/api/cameras/${cameraId}/recordings?${range}`),
      api('/api/recordings/status'),
    ]);
    spans = data.spans.map((span) => {
      const start = Date.parse(span.start);
      return { start, end: start + span.duration * 1000 };
    });
    motion = data.motion.map((event) => ({ start: event.start * 1000, end: event.end ? event.end * 1000 : null }));
    storage.textContent = `Grabaciones: ${gib(status.used_bytes)} de ${gib(status.budget_bytes)}`;
  } catch (error) {
    notify(error.message);
    return;
  }
  drawTimeline();
  drawEvents();
  if (!keepPlayback) {
    clip = null;
    video.removeAttribute('src');
    video.load();
    playhead.hidden = true;
    timeLabel.textContent = '--:--:--';
    overlay.lastElementChild.textContent = spans.length
      ? 'Pulsa en la línea de tiempo o en un evento para reproducir.'
      : 'No hay grabaciones este día.';
    overlay.classList.remove('is-hidden');
    updateDownload();
  }
}

function percent(ms) {
  return `${(Math.min(Math.max((ms - dayStart) / DAY_MS, 0), 1) * 100).toFixed(3)}%`;
}

function drawTimeline() {
  const bars = spans.map((span) => el('span', {
    class: 'timeline-rec',
    style: `left:${percent(span.start)};width:calc(${percent(span.end)} - ${percent(span.start)})`,
  }));
  const now = Date.now();
  const marks = motion.map((event) => el('span', {
    class: 'timeline-motion',
    title: `Movimiento ${clock(event.start)}`,
    style: `left:${percent(event.start)};width:max(2px, calc(${percent(event.end ?? now)} - ${percent(event.start)}))`,
  }));
  track.replaceChildren(...bars, ...marks);
}

function drawEvents() {
  eventsTitle.textContent = motion.length
    ? `${motion.length} evento${motion.length === 1 ? '' : 's'}`
    : 'Sin movimiento';
  if (!motion.length) {
    eventList.replaceChildren(el('li', { class: 'muted', text: 'No se detectó movimiento este día.' }));
    return;
  }
  eventList.replaceChildren(...[...motion].reverse().map((event) => {
    const seconds = event.end ? Math.max(1, Math.round((event.end - event.start) / 1000)) : null;
    return el('li', {},
      el('button', {
        type: 'button',
        class: 'event-item',
        // Start a few seconds early to see what triggered it.
        onclick: () => playAt(event.start - 5000),
      },
      el('strong', { text: clock(event.start) }),
      el('span', { class: 'muted', text: seconds ? `${seconds} s` : 'en curso' })),
    );
  }));
}

function currentTime() {
  return clip ? clip.start + video.currentTime * 1000 : null;
}

function updatePlayhead() {
  const now = currentTime();
  if (now == null) return;
  playhead.hidden = false;
  playhead.style.left = percent(now);
  timeLabel.textContent = clock(now);
  updateDownload();
}

function updateDownload() {
  const now = currentTime();
  const span = now != null && spans.find((item) => now >= item.start && now < item.end);
  if (!span) {
    download.setAttribute('aria-disabled', 'true');
    download.removeAttribute('href');
    return;
  }
  const seconds = Math.min(Number(clipSelect.value), (span.end - now) / 1000);
  const camera = cameras.find((item) => item.id === cameraSelect.value);
  const name = (camera?.name || 'camara').replace(/[^\p{L}\p{N}]+/gu, '_');
  download.href = `/api/cameras/${cameraSelect.value}/recordings/video?start=${encodeURIComponent(new Date(now).toISOString())}&duration=${seconds.toFixed(1)}&download=true`;
  download.download = `${name}_${fileStamp(now)}.mp4`;
  download.removeAttribute('aria-disabled');
}

function playAt(ms) {
  const span = spans.find((item) => ms < item.end && ms >= item.start) || spans.find((item) => item.start > ms);
  if (!span) {
    notify(ms > Date.now() - 5000 ? 'Ya estás en lo más reciente' : 'No hay grabación después de ese momento');
    return;
  }
  const start = Math.max(ms, span.start);
  // Stay a little behind the live edge: the newest segment is still being written.
  const end = Math.min(span.end, start + MAX_CLIP_S * 1000, Date.now() - 3000);
  if (end - start < 1000) {
    notify('Ese momento todavía se está grabando; prueba unos segundos antes');
    return;
  }
  clip = { start, end };
  video.src = `/api/cameras/${cameraSelect.value}/recordings/video?start=${encodeURIComponent(new Date(start).toISOString())}&duration=${((end - start) / 1000).toFixed(1)}`;
  video.playbackRate = speed;
  overlay.classList.add('is-hidden');
  video.play().catch(() => {});
  updatePlayhead();
}
