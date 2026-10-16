// Filter controls and transient candidate requests. Applied filters arrive from data.
import { api } from './api.js';
import { t } from './i18n.js';

const CATEGORY_PREVIEW_LIMIT = 3;
const KEYWORD_PREVIEW_LIMIT = 5;

const normalize = values => [...new Set(values.filter(value => typeof value === 'string').map(value => value.trim().toLowerCase()).filter(Boolean))];
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(text, action, key) {
  const node = el('button', 'btn btn-sm btn-outline', text);
  node.type = 'button'; node.onclick = action;
  if (key) {
    node.dataset.focusKey = key;
    node.id = 'memories-filter-' + encodeURIComponent(key);
  }
  return node;
}
function replaceKeepingFocus(root, nodes, fallback) {
  const active = document.activeElement;
  const key = root.contains(active) ? active.dataset.focusKey : null;
  const index = [...root.querySelectorAll('[data-focus-key]')].indexOf(active);
  root.replaceChildren(...nodes);
  if (index < 0) return;
  const controls = [...root.querySelectorAll('[data-focus-key]')].filter(node => !node.disabled);
  (controls.find(node => node.dataset.focusKey === key) || controls[Math.min(index, controls.length - 1)] || fallback)?.focus();
}

export function createValuePickers({root, onChange, onReset = () => {}, onRetryCategories, idPrefix = "memories", container = document.body, maxSelected = {categories: 20, keywords: 20}, allowCreate = false}) {
  const prefix = idPrefix;
  const cap = kind => maxSelected[kind] ?? Infinity;
  const makeButton = (text, action, key) => { const node = button(text, action, key); if (key) node.id = prefix + "-filter-" + encodeURIComponent(key); return node; };
  let state = {namespace: '', categories: [], keywords: []};
  let registry = {namespaces: [], categories: {}, categoryState: {status: 'idle', hasData: false}};
  let popup = null, timer = null, version = 0, context = '', namespaceSignature = '';
  let keywordSignature = '';
  let categorySignature = '';
  let suggestions = [], offset = 0, total = 0, status = 'idle', error = null;

  const nsLabel = el('label', 'memory-namespace-control'), namespace = el('select');
  nsLabel.id = prefix + '-namespace-control';
  nsLabel.append(el('span', 'memory-namespace-label', t('picker.namespace.label')));
  namespace.id = prefix + '-namespace'; nsLabel.append(namespace);
  const categories = makeButton(t('picker.categories')+' ▾', () => open('categories', categories));
  categories.classList.add('memory-filter-trigger');
  categories.id = prefix + '-categories';
  const categoryGroup = el('div', 'memory-category-control');
  categoryGroup.id = prefix + '-category-control';
  categoryGroup.setAttribute('role', 'group');
  categoryGroup.setAttribute('aria-label', t('picker.categories.aria'));
  const categoryPreview = el('div', 'memory-selected-preview');
  categoryPreview.id = prefix + '-selected-categories';
  categoryGroup.append(categories, categoryPreview);
  const keywordGroup = el('div', 'memory-keyword-control');
  keywordGroup.id = prefix + '-keyword-control';
  keywordGroup.setAttribute('role', 'group');
  keywordGroup.setAttribute('aria-label', t('picker.keywords.aria'));
  const selectedPreview = el('div', 'memory-selected-preview');
  selectedPreview.id = prefix + '-selected-preview';
  const keywords = makeButton(t('picker.keywords')+' ▾', () => open('keywords', keywords));
  keywords.classList.add('memory-filter-trigger');
  keywords.id = prefix + '-keywords';
  keywordGroup.append(keywords, selectedPreview);
  const reset = makeButton(t('picker.reset'), () => { close(); onReset(); }); reset.id = prefix + '-reset';
  reset.classList.add('memory-filter-reset');
  for (const trigger of [categories, keywords]) {
    trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-expanded', 'false');
  }
  root.replaceChildren(...(allowCreate ? [categoryGroup, keywordGroup] : [nsLabel, categoryGroup, keywordGroup, reset]));
  namespace.onchange = () => { close(); onChange({namespace: namespace.value}); };

  function invalidate() {
    ++version; clearTimeout(timer); timer = null;
  }
  function close(restore = false) {
    invalidate();
    if (!popup) return;
    const {node, trigger} = popup;
    popup = null; node.remove();
    trigger.setAttribute('aria-expanded', 'false'); trigger.removeAttribute('aria-controls');
    suggestions = []; offset = total = 0; status = 'idle'; error = null;
    if (restore && trigger.isConnected) trigger.focus();
  }
  function position() {
    if (!popup) return;
    const {node, trigger} = popup, box = trigger.getBoundingClientRect();
    node.style.left = Math.max(8, Math.min(box.left, window.innerWidth - node.offsetWidth - 8)) + 'px';
    node.style.top = Math.max(8, Math.min(box.bottom + 6, window.innerHeight - node.offsetHeight - 8)) + 'px';
  }
  function open(kind, trigger) {
    if (popup?.kind === kind) { close(true); return; }
    close();
    const node = el('div', 'memory-filter-popover'); node.id = prefix + '-' + kind + '-popover';
    node.setAttribute('role', 'dialog'); node.setAttribute('aria-label', kind === 'categories' ? t('picker.choose.category') : t('picker.choose.keyword'));
    const label = el('label', 'memory-filter-search', kind === 'categories' ? t('picker.search.categories') : t('picker.search.keywords'));
    label.id = prefix + '-' + kind + '-search-label';
    const input = el('input'); input.type = 'search'; input.id = prefix + '-' + kind + '-query';
    input.placeholder = kind === 'categories' ? t('picker.placeholder.category') : t('picker.placeholder.keyword');
    label.append(input);
    const selected = el('div', 'memory-filter-selected'), body = el('div', 'memory-filter-options');
    selected.id = prefix + '-' + kind + '-selected'; body.id = prefix + '-' + kind + '-options';
    if (kind === 'keywords') {
      body.classList.add('memory-keyword-suggestions');
      body.setAttribute('aria-labelledby', prefix + '-keywords-suggestions-title');
    }
    node.append(label, selected, body); container.append(node);
    popup = {kind, trigger, node, input, selected, body};
    trigger.setAttribute('aria-expanded', 'true'); trigger.setAttribute('aria-controls', node.id);
    if(allowCreate) input.onkeydown = event => {
      if(event.key!=='Enter'||event.isComposing)return;
      event.preventDefault();event.stopPropagation();
      const query=input.value.trim().toLowerCase();
      if(!query || normalize(state[kind]).includes(query) || state[kind].length>=cap(kind))return;
      const existing=kind==='categories' ? normalize(Object.keys(registry.categories)).includes(query) : suggestions.some(item=>item.keyword===query);
      if(existing) onChange({[kind]:[...state[kind],query]});
      else node.querySelector('[id="'+prefix+'-'+kind+'-create"]')?.click();
    };
    input.oninput = () => kind === 'categories' ? renderCategories() : resetSuggestions();
    if (kind === 'categories') renderCategories();
    else { renderSelected(); resetSuggestions(); }
    position(); input.focus();
  }
  function previewChip(value, dimension, index) {
    const remove = makeButton('', () => onChange({[dimension]: state[dimension].filter(item => item !== value)}),
      'preview:' + dimension + ':' + value + (allowCreate ? ':' + index : ''));
    remove.classList.add('memory-filter-chip');
    const label = el('span', 'memory-filter-chip-text', value), cross = el('span', 'memory-filter-chip-remove', ' ×');
    cross.setAttribute('aria-hidden', 'true');
    const pill = el('span', 'memory-filter-chip-pill'); pill.append(label, cross); remove.append(pill);
    remove.title = value; remove.setAttribute('aria-label', dimension === 'categories' ? t('picker.remove.category',{value}) : t('picker.remove.keyword',{value}));
    return remove;
  }
  function renderSelected() {
    if (!popup || (popup.kind !== 'keywords' && !allowCreate)) return;
    const kind = popup.kind;
    const nodes = [el('span', 'memory-filter-caption', t('picker.selected',{count:state[kind].length,cap:(Number.isFinite(cap(kind)) ? '/' + cap(kind) : '')}))];
    state[kind].forEach((value, index) => {
      const remove = makeButton(value + ' ×', () => onChange({[kind]: state[kind].filter((_, i) => i !== index)}), 'selected:' + value + (allowCreate ? ':' + index : ''));
      remove.title = value; remove.setAttribute('aria-label', t('picker.remove.keyword',{value})); nodes.push(remove);
    });
    replaceKeepingFocus(popup.selected, nodes, popup.input);
  }
  function renderCategories() {
    if (popup?.kind !== 'categories') return;
    if (allowCreate) renderSelected();
    const {input, body} = popup, info = registry.categoryState;
    const nodes = [], query = input.value.trim().toLowerCase();
    const values = normalize([...Object.keys(registry.categories), ...state.categories]).sort((a, b) => a.localeCompare(b));
    const known = new Set(normalize(Object.keys(registry.categories)));
    if (info.status === 'idle' || info.status === 'loading') nodes.push(el('p', 'text-muted', t('picker.categories.loading')));
    if (info.status === 'error') {
      const message = el('p', 'text-danger', t('picker.categories.failed',{error:info.error?.message}));
      message.setAttribute('role', 'alert'); nodes.push(message, makeButton(t('picker.retry'), onRetryCategories, 'retry'));
    }
    if (state.categories.length >= cap('categories')) nodes.push(el('p', 'text-muted', t('picker.categories.max',{count:cap('categories')})));
    for (const value of values.filter(value => value.includes(query))) {
      const label = el('label', 'memory-category-option'), checkbox = el('input'); checkbox.type = 'checkbox';
      label.id = prefix + '-category-option-' + encodeURIComponent(value);
      checkbox.id = prefix + '-category-' + encodeURIComponent(value);
      checkbox.checked = normalize(state.categories).includes(value);
      checkbox.disabled = !checkbox.checked && state.categories.length >= cap('categories');
      checkbox.dataset.focusKey = value; checkbox.dataset.value = value;
      checkbox.onchange = () => onChange({categories: checkbox.checked
        ? [...state.categories, value] : state.categories.filter(category => typeof category !== "string" || category.trim().toLowerCase() !== value)});
      label.append(checkbox, el('span', '', value + (!known.has(value) ? t('picker.categories.unavailable') : '')));
      nodes.push(label);
    }
    if (info.hasData && !values.some(value => value.includes(query))) nodes.push(el('p', 'text-muted', t('picker.categories.empty')));
    if (allowCreate && query && !known.has(query) && !state.categories.includes(query)) { const add = makeButton(t('picker.create',{value:query}), () => onChange({categories: [...state.categories, query]})); add.id = prefix + '-categories-create'; add.disabled = state.categories.length >= cap('categories'); nodes.push(add); }
    replaceKeepingFocus(body, nodes, input); position();
  }
  function renderSuggestions() {
    if (popup?.kind !== 'keywords') return;
    const heading = el('span', 'memory-filter-suggestions-title', t('picker.suggestions'));
    heading.id = prefix + '-keywords-suggestions-title';
    const nodes = [heading];
    if (state.keywords.length >= cap('keywords')) nodes.push(el('p', 'text-muted', t('picker.keywords.max',{count:cap('keywords')})));
    for (const item of suggestions) {
      const chosen = normalize(state.keywords).includes(item.keyword);
      const add = makeButton('',
        () => onChange({keywords: [...state.keywords, item.keyword]}), 'suggestion:' + item.keyword);
      add.classList.add('memory-keyword-suggestion');
      const icon = el('span', 'memory-keyword-suggestion-icon', chosen ? '✓' : '+');
      icon.setAttribute('aria-hidden', 'true');
      add.append(icon, el('span', 'memory-keyword-suggestion-text', item.keyword),
        el('span', 'memory-keyword-suggestion-count', '(' + item.count + ')'));
      add.disabled = chosen || state.keywords.length >= cap('keywords');
      add.title = item.keyword; add.setAttribute('aria-label', chosen ? t('picker.keyword.selected',{value:item.keyword}) : t('picker.keyword.add',{value:item.keyword}));
      add.dataset.keyword = item.keyword; nodes.push(add);
    }
    const message = el('p', 'text-muted', status === 'idle' ? t('picker.status.idle') : status === 'loading' ? t('picker.status.loading')
      : status === 'error' ? t('picker.status.error',{error:error.message}) : status === 'unknown' ? t('picker.status.unknown') : total ? t('picker.status.matches',{count:total}) : t('picker.status.empty'));
    message.setAttribute('role', status === 'error' ? 'alert' : 'status'); nodes.push(message);
    if (status === 'error') nodes.push(makeButton(t('picker.retry'), () => querySuggestions(offset > 0), 'retry'));
    else if (offset < total) {
      const more = makeButton(t('picker.more'), () => querySuggestions(true), 'more');
      more.id = prefix + '-keywords-more'; more.disabled = status === 'loading'; nodes.push(more);
    }
    const query = popup.input.value.trim().toLowerCase();
    if (allowCreate && query && !state.keywords.includes(query) && !suggestions.some(item => item.keyword === query)) { const add = makeButton(t('picker.create',{value:query}), () => onChange({keywords: [...state.keywords, query]})); add.id = prefix + '-keywords-create'; add.disabled = state.keywords.length >= cap('keywords'); nodes.push(add); }
    replaceKeepingFocus(popup.body, nodes, popup.input); position();
  }
  function resetSuggestions() {
    invalidate(); suggestions = []; offset = total = 0; error = null;
    if (popup?.kind !== 'keywords') return;
    status = popup.input.value.trim() ? 'loading' : 'idle'; renderSuggestions();
    if (status === 'loading') timer = setTimeout(() => { timer = null; querySuggestions(false); }, 250);
  }
  async function querySuggestions(more) {
    if (popup?.kind !== 'keywords' || !popup.input.value.trim()) return;
    if (allowCreate && !registry.namespaces.includes(state.namespace.trim())) { status = 'unknown'; renderSuggestions(); return; }
    const request = ++version, query = popup.input.value.trim(), start = more ? offset : 0;
    const body = {mode: 'literal', queries: [query], limit: 20, offset: start};
    if (state.namespace) body.namespace = state.namespace;
    if (state.categories.length) body.categories = [...state.categories];
    status = 'loading'; error = null; renderSuggestions();
    try {
      const response = await api('/keywords/query', {method: 'POST', body: JSON.stringify(body)});
      if (request !== version || popup?.kind !== 'keywords') return;
      const group = response.results?.[0];
      if (!group || !Array.isArray(group.items) || !Number.isSafeInteger(group.total) || group.total < 0
          || group.items.some(item => typeof item.keyword !== 'string' || !item.keyword.trim())
          || (!group.items.length && start < group.total)) throw new Error('Invalid keyword response');
      const values = new Map((more ? suggestions : []).map(item => [item.keyword, item]));
      group.items.forEach(item => { const keyword = item.keyword.trim().toLowerCase(); values.set(keyword, {...item, keyword}); });
      suggestions = [...values.values()]; offset = start + group.items.length; total = group.total;
      status = 'ready'; renderSuggestions();
    } catch (failure) {
      if (request !== version || popup?.kind !== 'keywords') return;
      error = failure; status = 'error'; renderSuggestions();
    }
  }
  function update(next, candidates) {
    state = next; registry = candidates;
    const signature = JSON.stringify([registry.namespaces, state.namespace]);
    if (signature !== namespaceSignature) {
      namespace.replaceChildren(new Option(t('picker.namespace.all'), ''), ...registry.namespaces.map(value => new Option(value, value)));
      if (state.namespace && !registry.namespaces.includes(state.namespace)) namespace.append(new Option(t('picker.namespace.unavailable',{name:state.namespace}), state.namespace));
      namespace.value = state.namespace; namespaceSignature = signature;
    }
    nsLabel.classList.toggle('has-filter', Boolean(state.namespace));
    categories.classList.toggle('has-filter', state.categories.length > 0);
    keywords.classList.toggle('has-filter', state.keywords.length > 0);
    categories.textContent = t('picker.categories') + (state.categories.length > CATEGORY_PREVIEW_LIMIT
      ? ' · +' + (state.categories.length - CATEGORY_PREVIEW_LIMIT) : '') + ' ▾';
    categories.setAttribute('aria-label', t('picker.categories.triggerAria',{count:state.categories.length}));
    const selectedCategorySignature = JSON.stringify(state.categories);
    if (categorySignature !== selectedCategorySignature) {
      replaceKeepingFocus(categoryPreview, state.categories.slice(0, CATEGORY_PREVIEW_LIMIT)
        .map((value,index) => previewChip(value, 'categories', index)), categories);
      categorySignature = selectedCategorySignature;
    }
    const selectedSignature = JSON.stringify(state.keywords);
    if (keywordSignature !== selectedSignature) {
      const nodes = state.keywords.slice(0, KEYWORD_PREVIEW_LIMIT).map((value,index) => previewChip(value, 'keywords', index));
      replaceKeepingFocus(selectedPreview, nodes, keywords); keywordSignature = selectedSignature;
    }
    keywords.textContent = t('picker.keywords') + (state.keywords.length > KEYWORD_PREVIEW_LIMIT
      ? ' · +' + (state.keywords.length - KEYWORD_PREVIEW_LIMIT) : '') + ' ▾';
    keywords.setAttribute('aria-label', t('picker.keywords.triggerAria',{count:state.keywords.length}));
    reset.hidden = !state.namespace && !state.categories.length && !state.keywords.length && !state.updatedAfter && !state.updatedBefore;
    reset.classList.toggle('has-filter', !reset.hidden);
    if (popup?.kind === 'categories') renderCategories();
    if (popup?.kind === 'keywords') { renderSelected(); renderSuggestions(); }
    const nextContext = JSON.stringify([state.namespace, state.categories]);
    if (nextContext !== context) { context = nextContext; resetSuggestions(); }
  }
  const outside = event => {
    // Selection can replace the clicked button before the event reaches document.
    const path = event.composedPath();
    const filterTrigger = event.composedPath().includes(categories) || event.composedPath().includes(keywords);
    if (popup && !path.includes(popup.node) && !filterTrigger) close();
  };
  const keyboard = event => {
    if (event.key === 'Escape' && popup && !event.isComposing && !event.defaultPrevented) { event.preventDefault(); close(true); }
  };
  const resize = () => close(), scroll = () => close();
  document.addEventListener('click', outside); document.addEventListener('keydown', keyboard);
  window.addEventListener('resize', resize); document.getElementById('content').addEventListener('scroll', scroll);
  return {update, close, reposition: position, isOpen: () => Boolean(popup), focusRoot: () => popup?.node, destroy() { close(); document.removeEventListener('click', outside); document.removeEventListener('keydown', keyboard); window.removeEventListener('resize', resize); document.getElementById('content').removeEventListener('scroll', scroll); }};
}
