// ui.js — transient notifications, inline error rendering and the debug overlay.

import { $ } from './core.js';

export function toast(msg, type = 'success') {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

export function showError(id, error) {
  const message = document.createElement('div');
  message.className = 'empty-state';
  message.textContent = 'Error: ' + error.message;
  $(id).replaceChildren(message);
}

export function toggleDebug() {
  document.body.classList.toggle('debug-ids');
  const on = document.body.classList.contains('debug-ids');
  $('btn-debug').querySelector('span').textContent = on ? 'Debug ON' : 'Debug';
  if (on) {
    document.addEventListener('click', onDebugClick, true);
  } else {
    document.removeEventListener('click', onDebugClick, true);
    document.querySelectorAll('.debug-focus').forEach(el => el.classList.remove('debug-focus'));
  }
}

function onDebugClick(e) {
  const el = e.target.closest('[id]');
  if (!el || !document.body.classList.contains('debug-ids')) {
    document.querySelectorAll('.debug-focus').forEach(f => f.classList.remove('debug-focus'));
    return;
  }
  if (el.classList.contains('debug-focus')) {
    el.classList.remove('debug-focus');
    return;
  }
  document.querySelectorAll('.debug-focus').forEach(f => f.classList.remove('debug-focus'));
  el.classList.add('debug-focus');
  navigator.clipboard.writeText(el.id).catch(() => {});
}
