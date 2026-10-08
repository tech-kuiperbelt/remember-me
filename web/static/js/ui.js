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

let debugLabel = null;

function showDebugLabel(element) {
  if (!debugLabel || !element) return;
  debugLabel.textContent = element.id;
  debugLabel.hidden = false;
  const box = element.getBoundingClientRect();
  debugLabel.style.left = Math.max(4, Math.min(box.left, innerWidth - debugLabel.offsetWidth - 4)) + 'px';
  debugLabel.style.top = Math.max(4, Math.min(box.bottom + 4, innerHeight - debugLabel.offsetHeight - 4)) + 'px';
}

function onDebugHover(event) {
  const element = event.target.closest('[id]');
  if (element && element.id !== 'btn-debug') showDebugLabel(element);
  else if (debugLabel) debugLabel.hidden = true;
}

function hideDebugLabel() {
  if (debugLabel) debugLabel.hidden = true;
}

export function toggleDebug() {
  document.body.classList.toggle('debug-ids');
  const on = document.body.classList.contains('debug-ids');
  $('btn-debug').setAttribute('aria-pressed', String(on));
  if (on) {
    debugLabel = document.createElement('div');
    debugLabel.className = 'debug-id-label';
    debugLabel.hidden = true;
    document.body.append(debugLabel);
    document.addEventListener('click', onDebugClick, true);
    document.addEventListener('mouseover', onDebugHover, true);
    document.addEventListener('scroll', hideDebugLabel, true);
    window.addEventListener('resize', hideDebugLabel);
  } else {
    document.removeEventListener('click', onDebugClick, true);
    document.removeEventListener('mouseover', onDebugHover, true);
    document.removeEventListener('scroll', hideDebugLabel, true);
    window.removeEventListener('resize', hideDebugLabel);
    debugLabel?.remove(); debugLabel = null;
    document.querySelectorAll('.debug-focus').forEach(el => el.classList.remove('debug-focus'));
  }
}

function onDebugClick(e) {
  if (e.target.closest('#btn-debug')) return;
  e.preventDefault(); e.stopImmediatePropagation();
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
  showDebugLabel(el);
  navigator.clipboard?.writeText(el.id).catch(() => {});
}
