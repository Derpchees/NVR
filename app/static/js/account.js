// "Cuenta" tab and the user chip: change panel username/password, log out.

import { api } from './api.js';
import { notify, setMessage } from './ui.js';

const form = document.querySelector('#account-form');
const message = document.querySelector('#account-message');
const userName = document.querySelector('#user-name');
const avatar = document.querySelector('#user-avatar');

function showUser(username) {
  userName.textContent = username;
  avatar.textContent = username.slice(0, 1);
  form.elements.username.value = username;
}

export async function init() {
  document.querySelector('#logout').addEventListener('click', async () => {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
    window.location.replace('/login');
  });
  form.addEventListener('submit', save);
  const me = await api('/api/auth/me');
  showUser(me.username);
}

async function save(event) {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const newPassword = form.elements.new_password.value;
  if (newPassword !== form.elements.confirm_password.value) {
    setMessage(message, 'Las contraseñas nuevas no coinciden.', 'error');
    return;
  }
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const result = await api('/api/auth/credentials', {
      method: 'PUT',
      body: {
        username: form.elements.username.value.trim(),
        new_password: newPassword || null,
        current_password: form.elements.current_password.value,
      },
    });
    form.reset();
    showUser(result.username);
    setMessage(message, 'Cambios guardados.', 'ok');
    notify('Credenciales actualizadas');
  } catch (error) {
    setMessage(message, error.message, 'error');
  } finally {
    button.disabled = false;
  }
}
