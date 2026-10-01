// detail.js — memory detail/edit modal shared by the list and search pages.

import { $, esc, formatTime, deriveSummary } from './core.js';
import { api, isNamespaceNotFound, confirmNamespaceCreation } from './api.js';
import { toast } from './ui.js';
import { getNamespaces, emitMemoriesChanged, emitStatsChanged } from './store.js';

let _currentDetailText = '';
let _copyBtnTimer = null;

async function viewDetail(id) {
  try {
    const m = await api('/memories/' + encodeURIComponent(id));
    showDetail(m, false);
  } catch(e) {
    toast(e.message, 'error');
  }
}

async function editMemory(id) {
  try {
    const m = await api('/memories/' + encodeURIComponent(id));
    showDetail(m, true);
  } catch(e) {
    toast(e.message, 'error');
  }
}

function showDetail(m, editable) {
  $('detail-title').textContent = 'Memory: ' + m.id.slice(0,8) + '…';
  _currentDetailText = m.text;
  const metaStr = JSON.stringify(m.metadata || {}, null, 2);

  const textHtml = editable
    ? `<div style="flex:1;display:flex;flex-direction:column"><textarea id="edit-text" style="flex:1;min-height:0;width:100%;padding:8px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px;font-family:inherit;resize:none">${esc(m.text)}</textarea></div>`
    : `<div class="value" style="white-space:pre-wrap">${esc(m.text)}</div>`;

  const nsHtml = editable
    ? '<div class="ns-wrapper"><input id="edit-namespace" autocomplete="off" style="padding:8px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px;width:100%"><div id="ns-dropdown" class="ns-dropdown"></div></div>'
    : `<span class="badge ns">${esc(m.namespace)}</span>`;

  const textFieldStyle = editable ? ' style="flex:1;display:flex;flex-direction:column;min-height:0"' : '';

  const actionsHtml = '<div id="detail-actions" style="display:flex;gap:8px"></div>';

  $('detail-body').setAttribute('style', 'padding:20px;overflow-y:auto;flex:1' + (editable ? ';display:flex;flex-direction:column' : ''));
  const summaryHtml = m.summary
    ? `<div class="value" style="white-space:pre-wrap">${esc(m.summary)}</div>`
    : `<div class="value" style="color:#999">(none)</div>`;
  $('detail-body').innerHTML = `
    <div class="detail-field"><div class="label">ID</div><div class="value">${esc(m.id)}</div></div>
    <div class="detail-field"><div class="label">Summary</div>${summaryHtml}</div>
    <div class="detail-field"${textFieldStyle}><div class="label">Text</div>${textHtml}</div>
    <div class="detail-field"><div class="label">Namespace</div><div class="value">${nsHtml}</div></div>
    <div class="detail-field"><div class="label">Created</div><div class="value">${formatTime(m.created_at) || '-'}</div></div>
    <div class="detail-field"><div class="label">Updated</div><div class="value">${formatTime(m.updated_at) || '-'}</div></div>
    <div class="detail-field"><div class="label">Score</div><div class="value">${m.score != null ? m.score.toFixed(6) : '-'}</div></div>
    <div class="detail-field"><div class="label">Metadata</div><div class="metadata-view">${metaStr === '{}' ? '<span style="color:#999">(empty)</span>' : esc(metaStr)}</div></div>
    <div style="margin-top:16px;display:flex;gap:8px;flex-shrink:0">${actionsHtml}</div>
  `;
  if (editable) {
    $('edit-namespace').value = m.namespace;
    $('edit-namespace').onfocus = showNsDropdown;
    $('edit-namespace').onblur = () => setTimeout(hideNsDropdown, 150);
    getNamespaces().forEach(ns => {
      const option = document.createElement('div');
      option.textContent = ns;
      option.onmousedown = () => pickNs(ns);
      $('ns-dropdown').append(option);
    });
  }
  const actions = $('detail-actions');
  const button = (label, fn, style='btn-outline') => {
    const el = document.createElement('button');
    el.className = 'btn btn-sm ' + style;
    el.textContent = label;
    el.onclick = () => fn(el);
    actions.append(el);
  };
  button('Copy', el => copyDetailText(el));
  if (editable) {
    button('Save', () => saveEdit(m.id), 'btn-primary');
    button('Cancel', closeDetail);
  } else {
    button('Edit', () => { closeDetail(); editMemory(m.id); });
    button('Delete', () => { closeDetail(); deleteMemory(m.id); }, 'btn-danger');
    button('Close', closeDetail);
  }
  $('detail-modal').style.display = 'flex';
}

async function saveEdit(id) {
  const textEl = $('edit-text');
  const nsEl = $('edit-namespace');
  if (!textEl || !nsEl) { toast('Form not ready, reopen the edit dialog', 'error'); return }
  const text = textEl.value;
  const namespace = nsEl.value;
  try {
    let res;
    try {
      res = await api('/memories/' + encodeURIComponent(id), {
        method: 'PUT',
        body: JSON.stringify({text, namespace}),
      });
    } catch(err) {
      if (!isNamespaceNotFound(err) || !confirmNamespaceCreation(err)) throw err;
      res = await api('/memories/' + encodeURIComponent(id), {
        method: 'PUT',
        body: JSON.stringify({text, namespace, confirm_codes: ['namespace_not_found']}),
      });
    }
    toast('Memory updated');
    closeDetail();
    emitMemoriesChanged();
    emitStatsChanged();
  } catch(e) {
    toast(e.message, 'error');
  }
}

function copyDetailText(btn) {
  const text = $('edit-text') ? $('edit-text').value : _currentDetailText;
  navigator.clipboard.writeText(text).then(() => {
    clearTimeout(_copyBtnTimer);
    btn.textContent = 'Copied!';
    btn.style.borderColor = '#4caf50';
    btn.style.color = '#4caf50';
    _copyBtnTimer = setTimeout(() => {
      btn.textContent = 'Copy';
      btn.style.borderColor = '';
      btn.style.color = '';
    }, 1200);
  }).catch(() => {
    toast('Copy failed (clipboard not available)', 'error');
  });
}

function closeDetail(e) {
  if (e && e.target !== e.currentTarget) return;
  $('detail-modal').style.display = 'none';
}

async function deleteMemory(id) {
  if (!confirm('Delete this memory?')) return;
  try {
    await api('/memories/' + encodeURIComponent(id), {method: 'DELETE'});
    toast('Memory deleted');
    emitMemoriesChanged();
  } catch(e) {
    toast(e.message, 'error');
  }
}

export function bindMemoryRows(root, memories) {
  root.querySelectorAll('tbody tr').forEach((row, i) => {
    const memory = memories[i];
    row.querySelector('.memory-text').title = memory.summary || deriveSummary(memory.text);
    row.querySelectorAll('[data-memory-action]').forEach(button => {
      button.onclick = () => {
        const action = button.dataset.memoryAction;
        if (action === 'viewDetail') viewDetail(memory.id);
        else if (action === 'editMemory') editMemory(memory.id);
        else if (action === 'deleteMemory') deleteMemory(memory.id);
      };
    });
  });
}

function showNsDropdown() {
  const dd = $('ns-dropdown');
  if (dd) dd.style.display = 'block';
}

function hideNsDropdown() {
  const dd = $('ns-dropdown');
  if (dd) dd.style.display = 'none';
}

function pickNs(ns) {
  $('edit-namespace').value = ns;
  hideNsDropdown();
}

export function mount() {
  $('detail-modal').addEventListener('click', closeDetail);
  $('btn-detail-close').addEventListener('click', () => closeDetail());
}
