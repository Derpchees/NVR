// Entry point: tab routing, camera loading and the clock.

import * as account from './account.js';
import { api } from './api.js';
import * as cameras from './cameras.js';
import * as live from './live.js';
import * as recordings from './recordings.js';
import * as system from './system.js';
import { notify, timeNow } from './ui.js';

const TABS = {
  live: { title: 'En vivo', subtitle: '' },
  recordings: { title: 'Grabaciones', subtitle: 'Grabación continua · las marcas naranjas son movimiento.' },
  cameras: { title: 'Cámaras', subtitle: 'Añade, busca o elimina cámaras de tu red.' },
  system: { title: 'Sistema', subtitle: 'Diagnóstico de los flujos de vídeo.' },
  account: { title: 'Cuenta', subtitle: 'Usuario y contraseña de acceso al panel.' },
};

function showTab() {
  const name = location.hash.slice(1) in TABS ? location.hash.slice(1) : 'live';
  for (const key of Object.keys(TABS)) {
    document.querySelector(`#tab-${key}`).hidden = key !== name;
  }
  document.querySelectorAll('.nav-item').forEach((item) => {
    const active = item.dataset.tab === name;
    item.classList.toggle('is-active', active);
    if (active) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });
  document.querySelector('#page-title').textContent = TABS[name].title;
  document.querySelector('#page-subtitle').textContent = TABS[name].subtitle;
  if (name === 'system') system.refresh();
  if (name === 'recordings') recordings.show();
  else recordings.hide();
}

async function loadCameras() {
  try {
    const list = await api('/api/cameras');
    document.querySelector('#nav-count').textContent = list.length;
    live.render(list);
    cameras.render(list);
    system.setCameras(list);
    recordings.setCameras(list);
    TABS.live.subtitle = `${list.length} cámara${list.length === 1 ? '' : 's'} · pulsa una para controlarla`;
    showTab();
  } catch (error) {
    notify(error.message);
  }
}

function tick() {
  const now = timeNow();
  document.querySelector('#clock').textContent = now;
  live.tick();
}

cameras.init(loadCameras);
recordings.init();
system.init();
window.addEventListener('hashchange', showTab);
showTab();
tick();
setInterval(tick, 1000);
account.init().catch((error) => notify(error.message));
loadCameras();
