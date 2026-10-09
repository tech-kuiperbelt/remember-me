// ui.js — transient notifications, inline error rendering and the debug overlay.

import { $ } from './core.js';

export const debugEnabled = () => document.documentElement.dataset.uiDebug === 'true';

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
const debugControl = 'button, input, textarea, select, a, summary, [contenteditable], [role="button"], [role="radio"]';
const debugNavigation = '[data-debug-toggle], [data-debug-navigation]';

function stopDebugAction(event) {
  event.preventDefault(); event.stopImmediatePropagation();
}

function onDebugPointerDown(event) {
  if (event.target.closest(debugNavigation) || event.button !== 0) return;
  // Stop native controls before focus, selection or a dropdown changes state.
  if (event.target.closest(debugControl)) stopDebugAction(event);
}

function onDebugKeyDown(event) {
  if (event.key === 'Escape') {
    stopDebugAction(event); toggleDebug(); const modal = $('detail-modal');
    const trigger = [...document.querySelectorAll('#detail-panel [data-debug-toggle]')].find(el => el.getClientRects().length && !el.closest('[inert]'));
    (modal.style.display === 'flex' ? trigger || $('btn-detail-close') : $('btn-debug')).focus(); return;
  }
  if (event.target.closest(debugNavigation)) return;
  // Tab may reach the drawer focus trap; editing and business shortcuts remain
  // blocked. beforeinput separately guards text changes.
  if (event.key !== 'Tab') event.stopImmediatePropagation();
  if (event.key !== 'Tab' && !event.ctrlKey && !event.metaKey && event.target.closest(debugControl)) {
    event.preventDefault();
  }
}

function onDebugEdit(event) {
  stopDebugAction(event);
}

const debugEvents = {click: onDebugClick, mouseover: onDebugHover, scroll: hideDebugLabel,
  pointerdown: onDebugPointerDown, mousedown: onDebugPointerDown, keydown: onDebugKeyDown,
  beforeinput: onDebugEdit, submit: onDebugEdit};

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
  if (element && !element.matches('[data-debug-toggle]')) showDebugLabel(element);
  else if (debugLabel) debugLabel.hidden = true;
}

function hideDebugLabel() {
  if (debugLabel) debugLabel.hidden = true;
}

export function toggleDebug() {
  if (!debugEnabled()) return;
  document.body.classList.toggle('debug-ids');
  const on = document.body.classList.contains('debug-ids');
  document.querySelectorAll('[data-debug-toggle]').forEach(el => el.setAttribute('aria-pressed', String(on)));
  if (on) {
    debugLabel = document.createElement('div');
    debugLabel.className = 'debug-id-label';
    debugLabel.hidden = true;
    document.body.append(debugLabel);
    for (const [type, handler] of Object.entries(debugEvents)) document.addEventListener(type, handler, true);
    window.addEventListener('resize', hideDebugLabel);
  } else {
    for (const [type, handler] of Object.entries(debugEvents)) document.removeEventListener(type, handler, true);
    window.removeEventListener('resize', hideDebugLabel);
    debugLabel?.remove(); debugLabel = null;
    document.querySelectorAll('.debug-focus').forEach(el => el.classList.remove('debug-focus'));
  }
}

function onDebugClick(e) {
  if (e.target.closest(debugNavigation)) return;
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
