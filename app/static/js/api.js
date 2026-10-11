// JSON calls to the panel API. A 401 means the session ended: go to login.

const FIELD_LABELS = {
  name: 'Nombre',
  host: 'Dirección IP',
  port: 'Puerto ONVIF',
  username: 'Usuario',
  password: 'Contraseña',
  new_password: 'Nueva contraseña',
  current_password: 'Contraseña actual',
};

function errorMessage(detail, status) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      const field = FIELD_LABELS[item.loc?.at(-1)];
      const message = item.msg === 'Field required' ? 'es obligatorio' : item.msg;
      return field ? `${field}: ${message}` : message;
    }).join('. ');
  }
  return `Error ${status}`;
}

export async function api(path, { method = 'GET', body } = {}) {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401 && !path.startsWith('/api/auth/')) {
    window.location.replace('/login');
    throw new Error('Sesión caducada');
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(errorMessage(payload.detail, response.status));
  }
  if (response.status === 204) return null;
  return response.json();
}
