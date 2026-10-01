// search.js — semantic search and exact (list) query modes, plus the keyword
// filter dropdown shared with the keywords page jump.

import { $, esc, formatTime, deriveSummary } from './core.js';
import { api } from './api.js';
import { toast, showError } from './ui.js';
import { getNamespaces, onNamespacesChanged } from './store.js';
import { bindMemoryRows } from './detail.js';
import { switchPage } from './router.js';

let dropdownRequest = 0;
let dropdownOffset = 0;

function switchSearchMode(mode) {
  document.querySelectorAll('#search-mode-tabs .ns-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`#search-mode-tabs .ns-tab[data-mode="${mode}"]`).classList.add('active');
  $('search-mode-semantic').style.display = mode === 'semantic' ? 'block' : 'none';
  $('search-mode-list').style.display = mode === 'list' ? 'block' : 'none';
  $('search-results').innerHTML = '';
}

async function doSearch(e) {
  e.preventDefault();
  const modeEl = document.querySelector('#search-mode-tabs .ns-tab.active');
  const mode = modeEl ? modeEl.dataset.mode : 'semantic';
  if (mode === 'list') return doListQuery();
  const q = $('search-query').value;
  if (!q.trim()) { toast('请输入搜索内容', 'error'); return; }
  const k = parseInt($('search-k').value) || 10;
  const threshold = parseFloat($('search-threshold').value) || 0;
  const ns = $('search-ns').value || undefined;
  $('search-results').innerHTML = '<div class="loading">Searching...</div>';
  try {
    const data = await api('/memories/search', {
      method: 'POST',
      body: JSON.stringify({query: q, k, threshold, namespace: ns}),
    });
    renderSearchResults(data);
  } catch(e) {
    showError('search-results', e);
  }
}

// List mode: mirrors GET /api/memories (same filters as the list tool)
async function doListQuery() {
  const params = new URLSearchParams();
  const ns = $('list-ns').value;
  if (ns) params.set('namespace', ns);
  params.set('limit', parseInt($('list-limit').value) || 100);
  params.set('offset', parseInt($('list-offset').value) || 0);
  params.set('sort_by', $('list-sort').value);
  const after = $('list-updated-after').value;
  const before = $('list-updated-before').value;
  if (after) params.set('updated_after', new Date(after).toISOString());
  if (before) params.set('updated_before', new Date(before).toISOString());
  Array.from($('list-keywords').selectedOptions).forEach(o => params.append('keywords', o.value));
  $('search-results').innerHTML = '<div class="loading">Loading...</div>';
  try {
    const data = await api('/memories?' + params.toString());
    renderListResults(data, params.get('sort_by'));
  } catch(e) {
    showError('search-results', e);
  }
}

function renderListResults(data, sortBy) {
  if (!data.length) {
    $('search-results').innerHTML = '<div class="empty-state">No memories found</div>';
    return;
  }
  const sortLabel = sortBy === 'updated_at' ? 'Updated' : 'Created';
  let html = '<table><thead><tr><th>Text</th><th>Namespace</th>' +
    `<th>${sortLabel}</th>` +
    '<th style="width:100px">Actions</th></tr></thead><tbody>';
  data.forEach(m => {
    const displayText = m.summary || deriveSummary(m.text);
    const ts = sortBy === 'updated_at' ? m.updated_at : m.created_at;
    html += `<tr>
      <td><div class="memory-text">${esc(displayText)}</div></td>
      <td><span class="badge ns">${esc(m.namespace)}</span></td>
      <td style="font-size:12px;color:#888">${formatTime(ts)}</td>
      <td><button class="btn btn-sm btn-outline" data-memory-action="viewDetail">View</button></td>
    </tr>`;
  });
  html += '</tbody></table>';
  $('search-results').innerHTML = html;
  bindMemoryRows($('search-results'), data);
}

function renderSearchResults(data) {
  if (!data.length) {
    $('search-results').innerHTML = '<div class="empty-state">No results found</div>';
    return;
  }
  let html = '<table><thead><tr><th>Score</th><th>Text</th><th>Namespace</th><th style="width:100px">Actions</th></tr></thead><tbody>';
  data.forEach(m => {
    const displayText = m.summary || deriveSummary(m.text);
    html += `<tr>
      <td><span class="badge">${(m.score * 100).toFixed(1)}%</span></td>
      <td><div class="memory-text">${esc(displayText)}</div></td>
      <td><span class="badge ns">${esc(m.namespace)}</span></td>
      <td><button class="btn btn-sm btn-outline" data-memory-action="viewDetail">View</button></td>
    </tr>`;
  });
  html += '</tbody></table>';
  $('search-results').innerHTML = html;
  bindMemoryRows($('search-results'), data);
}

function updateSearchNsDropdown() {
  for (const id of ['search-ns', 'list-ns']) {
    const sel = $(id), current = sel.value;
    sel.replaceChildren(new Option('(all)', ''), ...getNamespaces().map(ns => new Option(ns, ns)));
    sel.value = current;
  }
}

async function updateKeywordDropdown(more=false) {
  const request = ++dropdownRequest;
  if (!more) dropdownOffset = 0;
  const offset = dropdownOffset;
  const query = $('list-keyword-query').value.trim(), namespace = $('list-ns').value;
  const body = {mode: query ? 'literal' : 'browse', limit:50, offset};
  if (query) body.queries = [query];
  if (namespace) body.namespace = namespace;
  $('list-keyword-more').disabled = true;
  $('list-keyword-status').textContent = 'Loading...';
  try {
    const data = await api('/keywords/query', {method:'POST', body:JSON.stringify(body)});
    if (request !== dropdownRequest) return;
    const sel = $('list-keywords'), group = data.results[0];
    const selected = new Set(Array.from(sel.selectedOptions, o => o.value));
    const kept = Array.from(sel.options).filter(o => more || o.selected);
    const options = new Map(kept.map(o => [o.value, o]));
    group.items.forEach(item => options.set(item.keyword,
      new Option(`${item.keyword} (${item.count})`, item.keyword, false, selected.has(item.keyword))));
    sel.replaceChildren(...options.values());
    dropdownOffset = offset + group.items.length;
    $('list-keyword-more').disabled = dropdownOffset >= group.total;
    $('list-keyword-status').textContent = `${group.total} matches`;
  } catch(e) {
    if (request === dropdownRequest) $('list-keyword-status').textContent = 'Error: ' + e.message;
  }
}

// Jump from the keywords page into an exact query filtered by one keyword.
export async function showKeyword(keyword, namespace) {
  switchPage('search');
  switchSearchMode('list');
  $('list-ns').value = namespace;
  $('list-offset').value = '0';
  $('list-updated-after').value = $('list-updated-before').value = '';
  $('list-keyword-query').value = '';
  dropdownRequest++;
  $('list-keywords').replaceChildren(new Option(keyword, keyword, true, true));
  updateKeywordDropdown();
  doListQuery();
}

export function mount() {
  $('form-search').addEventListener('submit', doSearch);
  document.querySelectorAll('#search-mode-tabs .ns-tab').forEach(tab => {
    tab.addEventListener('click', () => switchSearchMode(tab.dataset.mode));
  });
  $('list-ns').addEventListener('change', () => updateKeywordDropdown());
  $('list-keyword-query').addEventListener('input', () => updateKeywordDropdown());
  $('list-keyword-more').addEventListener('click', () => updateKeywordDropdown(true));
  onNamespacesChanged(() => { updateSearchNsDropdown(); updateKeywordDropdown(); });
}
