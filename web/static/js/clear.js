// clear.js — destructive clear guarded by a generated 8-char confirmation code.

import { $ } from './core.js';
import { api } from './api.js';
import { emitMemoriesChanged, emitStatsChanged } from './store.js';

let _clearScope = '';
let _clearCode = '';

const CODE_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';

function genClearCode(len = 8) {
  let out = '';
  const arr = new Uint32Array(len);
  crypto.getRandomValues(arr);
  for (let i = 0; i < len; i++) out += CODE_CHARS[arr[i] % CODE_CHARS.length];
  return out;
}

function openClearModal(scope) {
  _clearScope = scope;
  _clearCode = genClearCode();
  $('clear-confirm-scope').textContent = scope;
  $('clear-confirm-code').textContent = _clearCode;
  const input = $('clear-confirm-input');
  input.value = '';
  input.focus();
  $('btn-clear-confirm').disabled = true;
  $('clear-modal').style.display = 'flex';
}

function checkClearCode(confirmOnMatch = false) {
  const ok = $('clear-confirm-input').value === _clearCode;
  $('btn-clear-confirm').disabled = !ok;
  if (confirmOnMatch && ok) doClearConfirmed();
}

function closeClearModal(e) {
  if (e && e.target !== e.currentTarget) return;
  $('clear-modal').style.display = 'none';
  _clearScope = '';
  _clearCode = '';
}

function doClearAll() {
  const ns = $('clear-ns').value.trim() || undefined;
  const scope = ns ? `namespace "${ns}"` : 'ALL namespaces';
  openClearModal(scope);
}

async function doClearConfirmed() {
  const ns = $('clear-ns').value.trim() || undefined;
  const resultEl = $('clear-result');
  const btn = $('btn-clear');
  closeClearModal();
  btn.disabled = true;
  resultEl.textContent = 'Clearing...';
  try {
    const q = ns ? '?namespace=' + encodeURIComponent(ns) : '';
    const result = await api('/memories' + q, {method: 'DELETE'});
    resultEl.textContent = `✅ Deleted ${result.deleted} memories.`;
    emitMemoriesChanged();
    emitStatsChanged();
  } catch(e) {
    resultEl.textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

export function mount() {
  $('btn-clear').addEventListener('click', doClearAll);
  $('clear-modal').addEventListener('click', closeClearModal);
  $('btn-clear-cancel').addEventListener('click', () => closeClearModal());
  $('btn-clear-confirm').addEventListener('click', doClearConfirmed);
  $('clear-confirm-input').addEventListener('input', () => checkClearCode());
  $('clear-confirm-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') checkClearCode(true);
  });
}
