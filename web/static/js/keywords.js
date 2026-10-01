// keywords.js — keyword browse/literal/semantic discovery with pagination.

import { $ } from './core.js';
import { api } from './api.js';
import { getNamespaces, onNamespacesChanged, getCategories, onCategoriesChanged } from './store.js';

let keywordRequest = 0;
let keywordOffset = 0;
// Set by mount() from the entry point so this module never imports the search page.
let openKeyword = () => {};

function updateKeywordNamespaces() {
  const sel = $('kw-ns'), value = sel.value;
  sel.replaceChildren(new Option('(all)', ''), ...getNamespaces().map(ns => new Option(ns, ns)));
  sel.value = value;
}

function updateKeywordCategories() {
  const sel = $('kw-categories');
  const selected = new Set([...sel.selectedOptions].map(o => o.value));
  const names = Object.keys(getCategories());
  sel.replaceChildren(...names.map(c => new Option(c, c, false, selected.has(c))));
  sel.size = Math.min(Math.max(names.length, 1), 6);
}

function invalidateKeywords() {
  keywordRequest++;
  keywordOffset = 0;
  $('kw-prev').disabled = $('kw-next').disabled = true;
  $('kw-results').textContent = 'Submit Search to update results.';
}

export async function loadKeywords(reset=false) {
  if (reset) keywordOffset = 0;
  const request = ++keywordRequest;
  const query = $('kw-query').value.trim();
  const mode = query ? $('kw-mode').value : 'browse';
  const semantic = mode === 'semantic';
  $('kw-sort').disabled = semantic;
  const body = {mode, limit: semantic ? 20 : 50, offset: semantic ? 0 : keywordOffset};
  const namespace = $('kw-ns').value;
  if (namespace) body.namespace = namespace;
  const categories = [...$('kw-categories').selectedOptions].map(o => o.value);
  if (categories.length) body.categories = categories;
  if (query) body.queries = [query];
  if (!semantic) body.sort_by = $('kw-sort').value;
  $('kw-results').textContent = 'Loading...';
  $('kw-prev').disabled = $('kw-next').disabled = true;
  try {
    const data = await api('/keywords/query', {method:'POST', body:JSON.stringify(body)});
    if (request !== keywordRequest) return;
    const group = data.results[0], root = $('kw-results');
    root.replaceChildren();
    const summary = document.createElement('p');
    summary.textContent = group.total ? `${group.total} matches · showing ${body.offset + 1}–${body.offset + group.items.length}` : 'No matching keywords.';
    root.append(summary);
    group.items.forEach(item => {
      const row = document.createElement('div'), button = document.createElement('button');
      button.className = 'btn btn-sm btn-outline';
      button.textContent = item.keyword;
      button.onclick = () => openKeyword(item.keyword, namespace);
      row.append(button, document.createTextNode(` · ${item.count} memories` + (semantic ? ` · score ${item.score.toFixed(3)}` : '')));
      root.append(row);
    });
    $('kw-prev').disabled = semantic || keywordOffset === 0;
    $('kw-next').disabled = semantic || keywordOffset + group.items.length >= group.total;
  } catch (e) {
    if (request === keywordRequest) $('kw-results').textContent = 'Error: ' + e.message;
  }
}

function keywordPage(direction) {
  keywordOffset = Math.max(0, keywordOffset + direction * 50);
  loadKeywords();
}

export function mount({ showKeyword = () => {} } = {}) {
  openKeyword = showKeyword;
  $('form-keywords').addEventListener('submit', e => { e.preventDefault(); loadKeywords(true); });
  $('kw-ns').addEventListener('change', () => loadKeywords(true));
  $('kw-categories').addEventListener('change', () => loadKeywords(true));
  $('kw-query').addEventListener('input', invalidateKeywords);
  $('kw-mode').addEventListener('change', () => loadKeywords(true));
  $('kw-sort').addEventListener('change', () => loadKeywords(true));
  $('kw-prev').addEventListener('click', () => keywordPage(-1));
  $('kw-next').addEventListener('click', () => keywordPage(1));
  onNamespacesChanged(updateKeywordNamespaces);
  onCategoriesChanged(updateKeywordCategories);
  updateKeywordCategories();
}
