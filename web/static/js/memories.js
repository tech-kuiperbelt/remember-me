// memories.js — memory list, sorting, pagination and namespace tabs.

import { $, esc, formatTime, deriveSummary } from './core.js';
import { api } from './api.js';
import { toast, showError } from './ui.js';
import { getNamespaces, getNamespaceCounts, onNamespacesChanged,
         onMaintenanceChanged, onMemoriesChanged } from './store.js';
import { bindMemoryRows } from './detail.js';
import { switchPage } from './router.js';

const MEMORIES_LIMIT = 20;
let memoriesOffset = 0;
let currentMemories = [];
let currentNamespace = '';
let sortField = 'created_at';
let sortDir = 'desc';

export async function loadMemories() {
  const params = new URLSearchParams({limit: MEMORIES_LIMIT, offset: memoriesOffset});
  if (currentNamespace) params.set('namespace', currentNamespace);
  $('memories-list').innerHTML = '<div class="loading">Loading...</div>';
  try {
    const data = await api('/memories?' + params.toString());
    currentMemories = data;
    renderMemories(data);
    updateNsTabs();
  } catch(e) {
    showError('memories-list', e);
  }
}

function toggleSort(field) {
  if (sortField === field) {
    sortDir = sortDir === 'asc' ? 'desc' : 'asc';
  } else {
    sortField = field;
    sortDir = 'asc';
  }
  renderMemories(currentMemories);
}

function sortMemories(data) {
  if (!sortField) return data;
  const sorted = [...data];
  sorted.sort((a, b) => {
    let va = a[sortField] || '';
    let vb = b[sortField] || '';
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va < vb) return sortDir === 'asc' ? -1 : 1;
    if (va > vb) return sortDir === 'asc' ? 1 : -1;
    return 0;
  });
  return sorted;
}

function sortArrow(field) {
  if (sortField !== field) return ' ↕';
  return sortDir === 'asc' ? ' ↑' : ' ↓';
}

function renderMemories(data) {
  if (!data.length) {
    $('memories-list').innerHTML = '<div class="empty-state">No memories found</div>';
    $('memories-pagination').innerHTML = '';
    return;
  }
  const sorted = sortMemories(data);
  let html = '<div class="table-scroll"><table id="memories-table"><thead><tr>' +
    `<th class="sortable" data-field="text">Text${sortArrow('text')}</th>` +
    `<th class="sortable" data-field="namespace">Namespace${sortArrow('namespace')}</th>` +
    `<th class="sortable" data-field="created_at">Created${sortArrow('created_at')}</th>` +
    '<th style="width:160px">Actions</th></tr></thead><tbody>';
  sorted.forEach(m => {
    const displayText = m.summary || deriveSummary(m.text);
    html += `<tr>
      <td><div class="memory-text">${esc(displayText)}</div></td>
      <td><span class="badge ns">${esc(m.namespace)}</span></td>
      <td style="font-size:12px;color:#888">${formatTime(m.created_at)}</td>
      <td><div class="actions">
        <button class="btn btn-sm btn-outline" data-memory-action="viewDetail">View</button>
        <button class="btn btn-sm btn-outline" data-memory-action="editMemory">Edit</button>
        <button class="btn btn-sm btn-danger" data-memory-action="deleteMemory">Del</button>
      </div></td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  $('memories-list').innerHTML = html;
  $('memories-list').querySelectorAll('th.sortable').forEach(th => {
    th.onclick = () => toggleSort(th.dataset.field);
  });
  bindMemoryRows($('memories-list'), sorted);

  // Pagination
  const hasMore = data.length >= MEMORIES_LIMIT;
  $('memories-pagination').innerHTML = `
    <button class="btn btn-sm btn-outline" data-nav="first" ${memoriesOffset <= 0 ? 'disabled' : ''}>First</button>
    <button class="btn btn-sm btn-outline" data-nav="prev" ${memoriesOffset <= 0 ? 'disabled' : ''}>Prev</button>
    <span style="color:#888">offset ${memoriesOffset}</span>
    <button class="btn btn-sm btn-outline" data-nav="next" ${hasMore ? '' : 'disabled'}>Next</button>
  `;
  const pagination = $('memories-pagination');
  pagination.querySelector('[data-nav="first"]').onclick = () => { memoriesOffset = 0; loadMemories(); };
  pagination.querySelector('[data-nav="prev"]').onclick = () => { memoriesOffset = Math.max(0, memoriesOffset - MEMORIES_LIMIT); loadMemories(); };
  pagination.querySelector('[data-nav="next"]').onclick = () => { memoriesOffset += MEMORIES_LIMIT; loadMemories(); };
}

function switchNamespace(el, ns) {
  currentNamespace = ns;
  document.querySelectorAll('#ns-tabs .ns-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  memoriesOffset = 0;
  loadMemories();
}

function updateNsTabs() {
  const container = $('ns-tabs');
  const counts = getNamespaceCounts();
  container.replaceChildren();
  for (const ns of ['', ...getNamespaces()]) {
    const tab = document.createElement('span');
    tab.className = 'ns-tab' + (ns === currentNamespace ? ' active' : '');
    tab.dataset.ns = ns;
    const count = counts[ns] ?? currentMemories.filter(m => m.namespace === ns).length;
    tab.textContent = ns === '' ? 'All Memories' : `${ns} (${count})`;
    tab.onclick = () => switchNamespace(tab, ns);
    container.append(tab);
  }
}

export function mount({ preselectClearNamespace = () => {} } = {}) {
  async function clearNamespace() {
    const ns = currentNamespace;
    if (!ns) { toast('Select a namespace first', 'error'); return }
    switchPage('ops');
    preselectClearNamespace(ns);
    toast('Enter maintenance mode, then use Clear in Ops.', 'info');
  }
  $('btn-clear-ns').addEventListener('click', clearNamespace);
  onMemoriesChanged(loadMemories);
  onNamespacesChanged(updateNsTabs);
  onMaintenanceChanged(state => { $('btn-clear-ns').disabled = state.state !== 'normal'; });
}
