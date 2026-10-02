// Filter controls and transient candidate requests. Applied filters arrive from data.
import { api } from './api.js';

const normalize = values => [...new Set(values.map(value => value.trim().toLowerCase()).filter(Boolean))];
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

export function createMemoriesFilters({root, onChange, onReset, onRetryCategories}) {
  let state = {namespace: '', categories: [], keywords: []};
  let registry = {namespaces: [], categories: {}, categoryState: {status: 'idle', hasData: false}};
  let popup = null, timer = null, version = 0, context = '', namespaceSignature = '';
  let keywordSignature = '';
  let categorySignature = '';
  let suggestions = [], offset = 0, total = 0, status = 'idle', error = null;

  const nsLabel = el('label', 'memory-namespace-control'), namespace = el('select');
  nsLabel.id = 'memories-namespace-control';
  nsLabel.append(el('span', 'memory-namespace-label', 'Namespace:'));
  namespace.id = 'memories-namespace'; nsLabel.append(namespace);
  const categories = button('Categories ▾', () => open('categories', categories));
  categories.classList.add('memory-filter-trigger');
  categories.id = 'memories-categories';
  const categoryGroup = el('div', 'memory-category-control');
  categoryGroup.id = 'memories-category-control';
  categoryGroup.setAttribute('role', 'group');
  categoryGroup.setAttribute('aria-label', '分类筛选');
  const categoryPreview = el('div', 'memory-selected-preview');
  categoryPreview.id = 'memories-selected-categories';
  categoryGroup.append(categories, categoryPreview);
  const keywordGroup = el('div', 'memory-keyword-control');
  keywordGroup.id = 'memories-keyword-control';
  keywordGroup.setAttribute('role', 'group');
  keywordGroup.setAttribute('aria-label', '关键词筛选');
  const selectedPreview = el('div', 'memory-selected-preview');
  selectedPreview.id = 'memories-selected-preview';
  const keywords = button('Keywords ▾', () => open('keywords', keywords));
  keywords.classList.add('memory-filter-trigger');
  keywords.id = 'memories-keywords';
  keywordGroup.append(keywords, selectedPreview);
  const reset = button('Reset', () => { close(); onReset(); }); reset.id = 'memories-reset';
  reset.classList.add('memory-filter-reset');
  for (const trigger of [categories, keywords]) {
    trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-expanded', 'false');
  }
  root.replaceChildren(nsLabel, categoryGroup, keywordGroup, reset);
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
    const node = el('div', 'memory-filter-popover'); node.id = 'memories-' + kind + '-popover';
    node.setAttribute('role', 'dialog'); node.setAttribute('aria-label', kind === 'categories' ? '选择分类' : '选择关键词');
    const label = el('label', 'memory-filter-search', kind === 'categories' ? 'Search categories' : 'Search keywords');
    const input = el('input'); input.type = 'search'; input.id = 'memories-' + kind + '-query';
    input.placeholder = kind === 'categories' ? '查找分类…' : '输入关键词查找…';
    label.append(input);
    const selected = el('div', 'memory-filter-selected'), body = el('div', 'memory-filter-options');
    if (kind === 'keywords') {
      body.classList.add('memory-keyword-suggestions');
      body.setAttribute('aria-labelledby', 'memories-keywords-suggestions-title');
    }
    node.append(label, selected, body); document.body.append(node);
    popup = {kind, trigger, node, input, selected, body};
    trigger.setAttribute('aria-expanded', 'true'); trigger.setAttribute('aria-controls', node.id);
    input.oninput = () => kind === 'categories' ? renderCategories() : resetSuggestions();
    if (kind === 'categories') renderCategories();
    else { renderSelected(); resetSuggestions(); }
    position(); input.focus();
  }
  function removeKeyword(value) {
    onChange({keywords: state.keywords.filter(word => word !== value)});
  }
  function previewChip(value, dimension) {
    const remove = button('', () => onChange({[dimension]: state[dimension].filter(item => item !== value)}),
      'preview:' + dimension + ':' + value);
    remove.classList.add('memory-filter-chip');
    const label = el('span', 'memory-filter-chip-text', value), cross = el('span', 'memory-filter-chip-remove', ' ×');
    cross.setAttribute('aria-hidden', 'true');
    const pill = el('span', 'memory-filter-chip-pill'); pill.append(label, cross); remove.append(pill);
    remove.title = value; remove.setAttribute('aria-label', (dimension === 'categories' ? '移除分类 ' : '移除关键词 ') + value);
    return remove;
  }
  function renderSelected() {
    if (popup?.kind !== 'keywords') return;
    const nodes = [el('span', 'memory-filter-caption', 'Selected (' + state.keywords.length + '/20)')];
    state.keywords.forEach(value => {
      const remove = button(value + ' ×', () => removeKeyword(value), 'selected:' + value);
      remove.title = value; remove.setAttribute('aria-label', '移除关键词 ' + value); nodes.push(remove);
    });
    replaceKeepingFocus(popup.selected, nodes, popup.input);
  }
  function renderCategories() {
    if (popup?.kind !== 'categories') return;
    const {input, body} = popup, info = registry.categoryState;
    const nodes = [], query = input.value.trim().toLowerCase();
    const values = normalize([...Object.keys(registry.categories), ...state.categories]).sort((a, b) => a.localeCompare(b));
    const known = new Set(normalize(Object.keys(registry.categories)));
    if (info.status === 'idle' || info.status === 'loading') nodes.push(el('p', 'text-muted', '正在加载分类…'));
    if (info.status === 'error') {
      const message = el('p', 'text-danger', '分类加载失败：' + info.error?.message);
      message.setAttribute('role', 'alert'); nodes.push(message, button('Retry', onRetryCategories, 'retry'));
    }
    if (state.categories.length === 20) nodes.push(el('p', 'text-muted', '最多选择 20 个分类；可取消已选项'));
    for (const value of values.filter(value => value.includes(query))) {
      const label = el('label', 'memory-category-option'), checkbox = el('input'); checkbox.type = 'checkbox';
      label.id = 'memories-category-option-' + encodeURIComponent(value);
      checkbox.id = 'memories-category-' + encodeURIComponent(value);
      checkbox.checked = state.categories.includes(value);
      checkbox.disabled = !checkbox.checked && state.categories.length >= 20;
      checkbox.dataset.focusKey = value; checkbox.dataset.value = value;
      checkbox.onchange = () => onChange({categories: checkbox.checked
        ? [...state.categories, value] : state.categories.filter(category => category !== value)});
      label.append(checkbox, el('span', '', value + (!known.has(value) ? '（当前候选中不可用）' : '')));
      nodes.push(label);
    }
    if (info.hasData && !values.some(value => value.includes(query))) nodes.push(el('p', 'text-muted', '没有匹配的分类'));
    replaceKeepingFocus(body, nodes, input); position();
  }
  function renderSuggestions() {
    if (popup?.kind !== 'keywords') return;
    const heading = el('span', 'memory-filter-suggestions-title', 'Suggestions');
    heading.id = 'memories-keywords-suggestions-title';
    const nodes = [heading];
    if (state.keywords.length === 20) nodes.push(el('p', 'text-muted', '最多选择 20 个关键词；可移除已选项'));
    for (const item of suggestions) {
      const chosen = state.keywords.includes(item.keyword);
      const add = button('',
        () => onChange({keywords: [...state.keywords, item.keyword]}), 'suggestion:' + item.keyword);
      add.classList.add('memory-keyword-suggestion');
      const icon = el('span', 'memory-keyword-suggestion-icon', chosen ? '✓' : '+');
      icon.setAttribute('aria-hidden', 'true');
      add.append(icon, el('span', 'memory-keyword-suggestion-text', item.keyword),
        el('span', 'memory-keyword-suggestion-count', '(' + item.count + ')'));
      add.disabled = chosen || state.keywords.length >= 20;
      add.title = item.keyword; add.setAttribute('aria-label', (chosen ? '已选择 ' : '添加关键词 ') + item.keyword);
      add.dataset.keyword = item.keyword; nodes.push(add);
    }
    const message = el('p', 'text-muted', status === 'idle' ? '输入关键词查找' : status === 'loading' ? '正在查找…'
      : status === 'error' ? '关键词加载失败：' + error.message : total ? total + ' matches' : '没有匹配的关键词');
    message.setAttribute('role', status === 'error' ? 'alert' : 'status'); nodes.push(message);
    if (status === 'error') nodes.push(button('Retry', () => querySuggestions(offset > 0), 'retry'));
    else if (offset < total) {
      const more = button('加载更多', () => querySuggestions(true), 'more');
      more.id = 'memories-keywords-more'; more.disabled = status === 'loading'; nodes.push(more);
    }
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
      namespace.replaceChildren(new Option('All', ''), ...registry.namespaces.map(value => new Option(value, value)));
      if (state.namespace && !registry.namespaces.includes(state.namespace)) namespace.append(new Option(state.namespace + '（已不可用）', state.namespace));
      namespace.value = state.namespace; namespaceSignature = signature;
    }
    nsLabel.classList.toggle('has-filter', Boolean(state.namespace));
    categories.classList.toggle('has-filter', state.categories.length > 0);
    keywords.classList.toggle('has-filter', state.keywords.length > 0);
    categories.textContent = 'Categories' + (state.categories.length > 3 ? ' · +' + (state.categories.length - 3) : '') + ' ▾';
    categories.setAttribute('aria-label', '选择分类，已选 ' + state.categories.length + ' 项');
    const selectedCategorySignature = JSON.stringify(state.categories);
    if (categorySignature !== selectedCategorySignature) {
      replaceKeepingFocus(categoryPreview, state.categories.slice(0, 3).map(value => previewChip(value, 'categories')), categories);
      categorySignature = selectedCategorySignature;
    }
    const selectedSignature = JSON.stringify(state.keywords);
    if (keywordSignature !== selectedSignature) {
      const nodes = state.keywords.slice(0, 5).map(value => previewChip(value, 'keywords'));
      replaceKeepingFocus(selectedPreview, nodes, keywords); keywordSignature = selectedSignature;
    }
    keywords.textContent = 'Keywords' + (state.keywords.length > 5 ? ' · +' + (state.keywords.length - 5) : '') + ' ▾';
    keywords.setAttribute('aria-label', '选择关键词，已选 ' + state.keywords.length + ' 项');
    reset.hidden = !state.namespace && !state.categories.length && !state.keywords.length && !state.updatedAfter && !state.updatedBefore;
    reset.classList.toggle('has-filter', !reset.hidden);
    if (popup?.kind === 'categories') renderCategories();
    if (popup?.kind === 'keywords') { renderSelected(); renderSuggestions(); }
    const nextContext = JSON.stringify([state.namespace, state.categories]);
    if (nextContext !== context) { context = nextContext; resetSuggestions(); }
  }
  document.addEventListener('click', event => {
    // Selection can replace the clicked button before the event reaches document.
    const path = event.composedPath();
    const filterTrigger = event.target.closest?.('#memories-categories, #memories-keywords');
    if (popup && !path.includes(popup.node) && !filterTrigger) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && popup) { event.preventDefault(); close(true); }
  });
  window.addEventListener('resize', () => close());
  document.getElementById('content').addEventListener('scroll', () => close());
  return {update, close};
}
