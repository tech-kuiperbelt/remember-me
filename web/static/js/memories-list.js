// Safe memory row and page rendering. All data/actions arrive through callbacks.
import { deriveSummary, formatTime, formatBytes } from './core.js';
import { createMemoryIcon } from './memory-icons.js';
import { t } from './i18n.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
const clean = value => String(value || '').trim().replace(/\s+/g, ' ');
const values = (raw, preserveCase = false) => Array.isArray(raw)
  ? [...new Set(raw.filter(v => typeof v === 'string').map(v => preserveCase ? v.trim() : v.trim().toLowerCase()).filter(Boolean))] : [];


function memoryDate(memory) {
  for (const stamp of [memory.updated_at, memory.created_at]) {
    if (stamp && Number.isFinite(Date.parse(stamp))) return stamp;
  }
  return null;
}

function displayDate(stamp) {
  if (!stamp) return '—';
  const date = new Date(stamp);
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
}

export function createMemoriesList({root, pagination, count, onOpen, onPage, onPageSize, onRetry, onConfigure, onAll, onReset, onClearSearch}) {
  let recentId = null, layouts = [], frame = null;
  const widths = new WeakMap();
  const observer = new ResizeObserver(entries => {
    if (entries.some(({target, contentRect}) => {
      const changed = widths.get(target) !== contentRect.width;
      widths.set(target, contentRect.width); return changed;
    })) scheduleLayout();
  });
  function scheduleLayout() {
    if (frame !== null) return;
    frame = requestAnimationFrame(() => { frame = null; layouts.forEach(fitMetadata); });
  }
  document.fonts?.ready.then(scheduleLayout);
  document.fonts?.addEventListener('loadingdone', scheduleLayout);
  function paintRecent() {
    root.querySelectorAll('.memory-row').forEach(row => row.classList.toggle('is-recent', row.dataset.memoryId === recentId));
  }
  function markRecent(id) { if (id) recentId = id; paintRecent(); }
  function clearRecent(id) { if (recentId === id) { recentId = null; paintRecent(); } }
  function fitMetadata(layout) {
    const {metadata, tags, groups, more, namespace} = layout;
    if (!metadata.getClientRects().length || !metadata.clientWidth) return;
    const hadFocus = document.activeElement === more;
    function show(count) {
      tags.forEach((tag, i) => { tag.hidden = i >= count; });
      groups.forEach(({node, first, label}) => {
        const empty = first.hidden;
        node.hidden = empty; label.hidden = empty;
      });
    }
    const fits = () => metadata.getBoundingClientRect().height <= 54.5
      && metadata.scrollWidth <= metadata.clientWidth + 1;
    if (namespace) namespace.style.maxWidth = '';
    more.hidden = true; more.style.width = ''; show(tags.length);
    if (fits()) {
      if (hadFocus) metadata.closest('.memory-row').querySelector('.memory-open').focus({preventScroll: true});
      metadata.dataset.hiddenCount = '0'; return;
    }
    more.textContent = '+' + tags.length; more.hidden = !tags.length;
    // Reserve the largest count so digit changes cannot disturb the measured fit.
    if (tags.length) {
      more.style.width = Math.ceil(more.getBoundingClientRect().width) + 'px';
      if (namespace) namespace.style.maxWidth = Math.min(160, Math.max(0, metadata.clientWidth - more.offsetWidth - 6)) + 'px';
    }
    let low = 0, high = tags.length;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2); show(middle);
      if (fits()) low = middle; else high = middle - 1;
    }
    function applyOverflow(count) {
      show(count); more.style.width = ''; more.textContent = '+' + (tags.length - count);
      if (namespace) namespace.style.maxWidth = Math.min(160, Math.max(0, metadata.clientWidth - more.offsetWidth - 6)) + 'px';
    }
    // Refine with the actual count width: +2 may admit a tag that +100 did not.
    applyOverflow(low);
    while (low > 0 && !fits()) applyOverflow(--low);
    while (low < tags.length - 1) {
      applyOverflow(low + 1);
      if (!fits()) { applyOverflow(low); break; }
      low++;
    }
    const hidden = tags.length - low;
    more.textContent = '+' + hidden; more.hidden = !hidden;
    more.setAttribute('aria-label', t('memories.tags.more', {count: hidden}));
    metadata.dataset.hiddenCount = String(hidden);
    if (hadFocus && hidden) more.focus({preventScroll: true});
  }

  function row(memory, searchMode, index) {
    // Position-based IDs let callers identify regions of the current page.
    // dataset.memoryId continues to identify the underlying memory record.
    const rowId = 'memory-row-' + (index + 1);
    const title = clean(memory.summary) || deriveSummary(memory.text) || t('memories.emptyContent');
    const body = clean(memory.text), prefix = title.replace(/…$/, '');
    const remainder = body.startsWith(prefix) ? body.slice(prefix.length).trim() : body;
    const preview = Array.from(remainder).slice(0, 256).join('');
    const item = el('div', 'memory-row');
    item.id = rowId;
    item.dataset.memoryId = memory.id;
    const open = el('button', 'memory-open'); open.type = 'button';
    open.setAttribute('aria-label', t('memories.open.aria', {title}));
    open.title = title;
    open.onclick = () => onOpen(memory.id);
    const icon = el('span', 'memory-row-icon'); icon.setAttribute('aria-hidden', 'true');
    const documentIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    documentIcon.setAttribute('viewBox', '0 0 24 24');
    documentIcon.setAttribute('width', '16'); documentIcon.setAttribute('height', '16');
    documentIcon.setAttribute('fill', 'none'); documentIcon.setAttribute('stroke', 'currentColor');
    documentIcon.setAttribute('stroke-width', '1.8'); documentIcon.setAttribute('stroke-linecap', 'round');
    documentIcon.setAttribute('stroke-linejoin', 'round'); documentIcon.setAttribute('focusable', 'false');
    const documentPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    documentPath.setAttribute('d', 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z M14 2v6h6 M8 13h8 M8 17h6');
    documentIcon.append(documentPath); icon.append(documentIcon);
    const content = el('div', 'memory-row-content');
    const heading = el('div', 'memory-row-title', title); heading.title = title;
    const summary = el('div', 'memory-row-summary', preview || '\u00a0');
    if (!preview) summary.setAttribute('aria-hidden', 'true');
    content.append(heading);
    if (preview) content.append(summary);
    const footer = el('div', 'memory-row-footer');
    const metadata = el('div', 'memory-row-metadata');
    if (searchMode) {
      const label = ({semantic: t('memories.mode.semantic'), lexical: t('memories.mode.lexical'), anchor: t('memories.mode.anchor')})[searchMode];
      const score = el('span', 'memory-chip memory-search-score',
        label + ' ' + memory.score.toFixed(3));
      score.title = label + ' ' + String(memory.score);
      score.id = rowId + '-score';
      metadata.append(score);
    }
    const namespace = memory.namespace ? el('div', 'memory-tag-group memory-namespace') : null;
    if (namespace) {
      namespace.title = memory.namespace;
      namespace.setAttribute('aria-label', t('memories.namespace.aria', {name: memory.namespace}));
      namespace.append(createMemoryIcon('namespace'), el('span', 'memory-namespace-value', memory.namespace));
      metadata.append(namespace);
    }
    const tagNodes = [], groups = [];
    for (const [field, dimension, label] of [['categories', 'category', t('field.categories')], ['keywords', 'keyword', t('field.keywords')], ['anchors', 'anchor', t('field.anchors')]]) {
      const tags = values(memory.metadata?.[field], field === 'anchors');
      if (!tags.length) continue;
      const group = el('span', 'memory-tag-group'); group.id = rowId + '-' + field;
      const firstPair = el('span', 'memory-tag-first');
      const groupLabel = el('span', 'memory-group-label');
      groupLabel.title = label;
      groupLabel.append(createMemoryIcon(dimension));
      group.setAttribute('aria-label', label + ': ' + tags.join(', '));
      firstPair.append(groupLabel); group.append(firstPair);
      tags.forEach((value, tagIndex) => {
        const chip = el('span', 'memory-chip memory-' + dimension + '-chip', value);
        chip.title = label + ': ' + value;
        chip.id = rowId + '-' + dimension + '-' + (tagIndex + 1);
        (tagIndex === 0 ? firstPair : group).append(chip); tagNodes.push(chip);
      });
      groups.push({node: group, first: tagNodes[tagNodes.length - tags.length], label: firstPair});
      metadata.append(group);
    }
    const more = el('button', 'memory-chip memory-tags-more');
    more.id = rowId + '-tags-more'; more.type = 'button'; more.hidden = true;
    more.onclick = () => onOpen(memory.id); metadata.append(more);
    layouts.push({metadata, tags: tagNodes, groups, more, namespace});
    const stamp = memoryDate(memory), time = el('span', 'memory-row-time', displayDate(stamp));
    time.title = stamp ? formatTime(stamp) : t('memories.time.invalid');
    for (const [region, node] of Object.entries({open, icon, content, title: heading, summary,
      footer, metadata, namespace, date: time})) if (node) node.id = rowId + '-' + region;
    if (stamp) open.setAttribute('aria-label', t('memories.open.time.aria', {title, time: formatTime(stamp)}));
    footer.append(metadata, time); content.append(footer);
    const detailArrow = el('button', 'memory-row-detail-arrow', '›');
    detailArrow.id = rowId + '-detail-arrow';
    detailArrow.type = 'button'; detailArrow.tabIndex = -1; detailArrow.dataset.debugNavigation = '';
    detailArrow.setAttribute('aria-label', t('memories.detail.aria', {title}));
    detailArrow.onclick = () => onOpen(memory.id);
    item.append(open, icon, content, detailArrow);
    item.addEventListener('click', event => {
      if (!event.target.closest('button')) onOpen(memory.id);
    });
    return item;
  }

  function button(text, action, disabled = false) {
    const node = el('button', 'btn btn-sm btn-outline', text);
    node.type = 'button'; node.disabled = disabled; node.onclick = action; return node;
  }

  function render(state) {
    observer.disconnect(); layouts = []; root.replaceChildren(); pagination.replaceChildren();
    root.setAttribute('aria-busy', String(state.status === 'loading'));
    const searching = Boolean(state.submittedQuery);
    const resolution = document.getElementById('memories-filter-resolution'); resolution.replaceChildren();
    if (searching && state.searchMode === 'semantic' && state.status === 'ready' && state.filterResolution) {
      for (const [dimension, label] of [['keywords', t('field.keywords')], ['categories', t('field.categories')]]) {
        for (const mapping of state.filterResolution[dimension] || []) {
          if (mapping.match_type === 'exact') continue;
          const text = mapping.match_type === 'none' ? t('memories.filter.notfound', {label, input: mapping.input})
            : label + ': ' + mapping.input + ' → ' + mapping.matches.filter(match => match.accepted).map(match => match.value).join(', ');
          resolution.append(el('div', '', text));
        }
      }
    }
    count.textContent = state.status === 'ready' ? (searching ? t('memories.count.search', {total: state.total, k: state.k}) : t('memories.count.browse', {total: state.total})) : state.status === 'model_pending' ? t('memories.count.preparing') : state.status === 'error' ? t('memories.count.error') : t('memories.count.loading');
    if (state.status === 'model_pending') {
      const model = state.model || {}, pending = el('div', 'memory-state');
      pending.setAttribute('role', 'status');
      const labels = {waiting: 'model.waiting', downloading: 'model.downloading', validating: 'model.validating', cancelling: 'model.cancelling', failed: 'model.failed', unavailable: 'model.unavailable'};
      const statusText = model.status === 'failed' ? 'memories.model.failed'
        : model.status === 'unavailable' ? 'model.unavailable'
        : 'memories.model.waiting';
      pending.append(el('p', '', t(labels[model.status] || 'memories.count.preparing')),
        el('p', '', t(statusText)));
      if (['downloading', 'validating', 'cancelling'].includes(model.status)) {
        const progress = el('progress'); progress.max = 100;
        progress.setAttribute('aria-label', t('ops.model.progress.aria'));
        if (Number.isFinite(model.percent)) progress.value = model.percent;
        pending.append(progress);
        if (model.total) pending.append(el('p', '', t('model.progress.detail', {
          file: model.file || t('model.progress.file'), completed: formatBytes(model.completed || 0),
          total: formatBytes(model.total), percent: Number.isFinite(model.percent) ? ' (' + model.percent.toFixed(1) + '%)' : ''})));
      }
      pending.append(button(t('memories.model.configure'), onConfigure)); root.append(pending);
    } else if (state.status === 'loading' || state.status === 'idle') {
      const skeleton = el('div', 'memory-skeletons'); skeleton.setAttribute('aria-label', t('memories.list.loading.aria'));
      for (let i = 0; i < 5; i++) skeleton.append(el('div', 'memory-skeleton'));
      root.append(skeleton);
    } else if (state.status === 'error') {
      const error = el('div', 'memory-state'); error.setAttribute('role', 'alert');
      error.append(el('p', '', 'Error: ' + state.error.message), button(t('memories.retry'), onRetry));
      if (state.error.payload?.response_code === 'namespace_not_found') error.append(button(t('memories.back.all'), onAll));
      root.append(error);
    } else if (!state.items.length) {
      const filtered = state.namespace || state.categories.length || state.keywords.length
        || state.updatedAfter || state.updatedBefore;
      const empty = el('div', 'memory-state');
      empty.append(el('p', '', filtered || searching ? t('memories.empty.filtered') : t('memories.empty.none')));
      if (filtered) empty.append(button(t('memories.clearFilters'), onReset));
      else if (searching) empty.append(button(t('memories.clearSearch'), onClearSearch));
      root.append(empty);
    } else state.items.forEach((memory, index) => root.append(row(memory, searching ? state.searchMode : null, index)));

    const ready = state.status === 'ready', last = Math.max(1, Math.ceil(state.total / state.pageSize));
    const start = ready && state.total ? (state.page - 1) * state.pageSize + 1 : 0;
    const end = ready ? Math.min(state.page * state.pageSize, state.total) : 0;
    pagination.append(el('span', 'memories-range', ready ? (searching ? t('memories.range.search', {start, end, total: state.total}) : t('memories.range.browse', {start, end, total: state.total})) : ''));
    const nav = el('nav', 'memories-pages'); nav.setAttribute('aria-label', t('memories.pagination.aria'));
    if (ready && state.total) {
      nav.append(button('‹', () => onPage(state.page - 1), state.page === 1));
      nav.lastChild.setAttribute('aria-label', t('memories.pagination.prev'));
      const pages = [...new Set([1, last, state.page - 1, state.page, state.page + 1])]
        .filter(page => page > 0 && page <= last).sort((a, b) => a - b);
      let previous = 0;
      for (const page of pages) {
        if (previous && page > previous + 1) nav.append(el('span', 'text-muted', '…'));
        const current = button(String(page), () => onPage(page), page === state.page);
        if (page === state.page) current.setAttribute('aria-current', 'page');
        nav.append(current); previous = page;
      }
      nav.append(button('›', () => onPage(state.page + 1), state.page === last));
      nav.lastChild.setAttribute('aria-label', t('memories.pagination.next'));
    }
    observer.observe(root); layouts.forEach(({metadata}) => observer.observe(metadata));
    layouts.forEach(fitMetadata);
    pagination.append(nav);
    const size = el('label', 'memories-size', t('memories.pageSize') + ' '), select = el('select'); select.id = 'memories-page-size';
    for (const number of [10, 20, 50]) select.append(new Option(String(number), String(number)));
    select.value = state.pageSize;
    select.onchange = () => onPageSize(Number(select.value)); size.append(select); pagination.append(size);
  }
  return {render(state) { render(state); paintRecent(); }, markRecent, clearRecent};
}
