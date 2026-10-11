// "Cámaras" tab: inventory list, add dialog and network discovery.

import { api } from './api.js';
import { el, icon, iconButton, notify, setMessage } from './ui.js';

const list = document.querySelector('#camera-list');
const cameraDialog = document.querySelector('#camera-modal');
const form = document.querySelector('#camera-form');
const formMessage = document.querySelector('#form-message');
const saveButton = document.querySelector('#save-camera');
const testButton = document.querySelector('#test-camera');
const discoveryDialog = document.querySelector('#discover-modal');
const discoveryStatus = document.querySelector('#discovery-status');
const discoveryList = document.querySelector('#discovery-list');
const scanAgain = document.querySelector('#scan-again');

let onChange = () => {};

export function init(changed) {
  onChange = changed;
  document.querySelector('#add-open').addEventListener('click', () => openAdd());
  document.querySelector('#discover-open').addEventListener('click', scan);
  scanAgain.addEventListener('click', scan);
  document.querySelectorAll('[data-close]').forEach((button) => {
    button.addEventListener('click', () => document.getElementById(button.dataset.close).close());
  });
  // Any edit after a successful test must be tested again before saving.
  form.addEventListener('input', () => { saveButton.disabled = true; });
  testButton.addEventListener('click', testConnection);
  form.addEventListener('submit', save);
}

export function render(cameras) {
  if (!cameras.length) {
    list.replaceChildren(el('div', { class: 'empty' },
      el('span', { class: 'empty-icon' }, icon('camera')),
      el('h2', { text: 'Sin cámaras' }),
      el('p', { class: 'muted', text: 'Busca cámaras ONVIF en tu red o añádelas con su dirección IP.' }),
    ));
    return;
  }
  list.replaceChildren(...cameras.map((camera) => {
    const chips = el('div', { class: 'chips' }, el('span', { class: 'badge', text: 'Detectando…' }));
    loadCapabilities(camera, chips);
    return el('div', { class: 'camera-row' },
      el('span', { class: 'row-icon' }, icon('camera')),
      el('div', {}, el('strong', { text: camera.name }), el('span', { class: 'muted', text: `${camera.host}:${camera.port}${camera.username ? ` · ${camera.username}` : ''}` })),
      chips,
      iconButton('Eliminar cámara', 'trash', () => remove(camera), 'icon-button button-danger'),
    );
  }));
}

async function loadCapabilities(camera, chips) {
  try {
    const caps = await api(`/api/cameras/${camera.id}/controls`);
    const found = [
      caps.resolution && caps.resolution,
      caps.ir_cut_modes.length && 'Día/noche',
      caps.light_commands.length && 'Luz',
      caps.microphone && 'Micrófono',
      caps.speaker && 'Altavoz',
      caps.ptz && 'PTZ',
      caps.relays.length && `${caps.relays.length} salida${caps.relays.length === 1 ? '' : 's'}`,
    ].filter(Boolean);
    chips.replaceChildren(...found.map((text) => el('span', { class: 'badge', text })));
  } catch {
    chips.replaceChildren(el('span', { class: 'badge badge-error', text: 'Sin respuesta ONVIF' }));
  }
}

function payload() {
  return {
    name: form.elements.name.value.trim(),
    host: form.elements.host.value.trim(),
    port: Number(form.elements.port.value),
    username: form.elements.username.value,
    password: form.elements.password.value,
  };
}

function openAdd(host = '', name = '', port = 80) {
  form.reset();
  form.elements.host.value = host;
  form.elements.name.value = name;
  form.elements.port.value = String(port);
  saveButton.disabled = true;
  setMessage(formMessage, '');
  cameraDialog.showModal();
}

async function testConnection() {
  if (!form.reportValidity()) return;
  testButton.disabled = true;
  setMessage(formMessage, 'Consultando la cámara…');
  try {
    const { best, profiles } = await api('/api/cameras/test', { method: 'POST', body: payload() });
    const size = best.width && best.height ? `${best.width}×${best.height}` : best.name;
    setMessage(formMessage, `Conexión correcta. Se usará la mejor calidad disponible: ${size} (de ${profiles} perfil${profiles === 1 ? '' : 'es'}).`, 'ok');
    saveButton.disabled = false;
  } catch (error) {
    setMessage(formMessage, error.message, 'error');
    saveButton.disabled = true;
  } finally {
    testButton.disabled = false;
  }
}

async function save(event) {
  event.preventDefault();
  saveButton.disabled = true;
  setMessage(formMessage, 'Guardando…');
  try {
    await api('/api/cameras', { method: 'POST', body: payload() });
    cameraDialog.close();
    notify('Cámara añadida');
    await onChange();
  } catch (error) {
    setMessage(formMessage, error.message, 'error');
    saveButton.disabled = false;
  }
}

async function remove(camera) {
  if (!window.confirm(`¿Eliminar la cámara «${camera.name}»?`)) return;
  try {
    await api(`/api/cameras/${camera.id}`, { method: 'DELETE' });
    notify('Cámara eliminada');
    await onChange();
  } catch (error) {
    notify(error.message);
  }
}

async function scan() {
  if (!discoveryDialog.open) discoveryDialog.showModal();
  discoveryStatus.textContent = 'Buscando cámaras en la red local…';
  discoveryList.replaceChildren(el('div', { class: 'spinner' }));
  scanAgain.disabled = true;
  try {
    const devices = await api('/api/cameras/discover', { method: 'POST' });
    discoveryStatus.textContent = devices.length
      ? `${devices.length} dispositivo${devices.length === 1 ? '' : 's'} encontrado${devices.length === 1 ? '' : 's'}`
      : 'No se encontró ninguna cámara.';
    discoveryList.replaceChildren(...devices.map((device) => el('div', { class: 'discovery-item' },
      el('div', {}, el('strong', { text: device.name }), el('span', { text: `${device.host}:${device.port}` })),
      el('button', {
        class: 'button',
        type: 'button',
        text: 'Añadir',
        onclick: () => {
          discoveryDialog.close();
          openAdd(device.host, device.name, device.port);
        },
      }),
    )));
  } catch (error) {
    discoveryStatus.textContent = error.message;
    discoveryList.replaceChildren();
  } finally {
    scanAgain.disabled = false;
  }
}
