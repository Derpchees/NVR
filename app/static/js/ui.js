// Small DOM helpers shared by every view.

const toast = document.querySelector('#toast');
let toastTimer;

export function notify(message) {
  toast.textContent = message;
  toast.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 3600);
}

export function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === false || value == null) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  node.append(...children.filter((child) => child != null));
  return node;
}

const ICONS = {
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  reload: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  left: '<path d="m15 6-6 6 6 6"/>',
  right: '<path d="m9 6 6 6-6 6"/>',
  'up-left': '<path d="M16 8H8v8M8 8l9 9"/>',
  'up-right': '<path d="M8 8h8v8M16 8l-9 9"/>',
  'down-left': '<path d="M8 8v8h8M8 16l9-9"/>',
  'down-right': '<path d="M16 8v8H8M16 16 7 7"/>',
  stop: '<rect x="8" y="8" width="8" height="8" rx="1.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  sound: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
  muted: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="m16 9.5 5 5M21 9.5l-5 5"/>',
  sparkle: '<path d="M12 3.5l1.8 5.2 5.2 1.8-5.2 1.8L12 17.5l-1.8-5.2L5 10.5l5.2-1.8z"/><path d="M18.5 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
  camera: '<path d="M3 7.5h12.5L21 4.5v15l-5.5-3H3z"/><circle cx="8" cy="12" r="2.2"/>',
};

export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name];
  return svg;
}

export function iconButton(label, name, onclick, className = 'icon-button') {
  const button = el('button', { class: className, type: 'button', title: label, 'aria-label': label, onclick });
  button.append(icon(name));
  return button;
}

export function setMessage(target, message, kind = '') {
  target.textContent = message;
  target.classList.toggle('is-error', kind === 'error');
  target.classList.toggle('is-ok', kind === 'ok');
}

export function timeNow() {
  return new Date().toLocaleTimeString('es-MX', { hour12: false });
}
