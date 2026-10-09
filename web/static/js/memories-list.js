// Safe memory row and page rendering. All data/actions arrive through callbacks.
import { deriveSummary, formatTime } from './core.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
const clean = value => String(value || '').trim().replace(/\s+/g, ' ');
const values = raw => Array.isArray(raw)
  ? [...new Set(raw.filter(v => typeof v === 'string').map(v => v.trim().toLowerCase()).filter(Boolean))] : [];

function memoryDate(memory) {
  for (const stamp of [memory.updated_at, memory.created_at]) {
    if (stamp && Number.isFinite(Date.parse(stamp))) return stamp;
  }
  return null;
}

function displayDate(stamp) {
  if (!stamp) return '—';
  return new Date(stamp).toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'});
}

export function createMemoriesList({root, pagination, count, onOpen, onPage, onPageSize, onRetry, onAll, onReset, onClearSearch}) {
  let popup = null, selectedId = null;
  function setSelected(id) { selectedId = id; root.querySelectorAll(".memory-row").forEach(row => row.classList.toggle("is-selected", row.dataset.memoryId === id)); }
  function closeTags(restore = false) {
    if (!popup) return;
    const {node, trigger} = popup;
    popup = null; node.remove(); trigger.setAttribute('aria-expanded', 'false');
    if (restore && trigger.isConnected) trigger.focus();
  }
  document.addEventListener('click', event => {
    if (popup && !popup.node.contains(event.target) && event.target !== popup.trigger) closeTags(true);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && popup) { event.preventDefault(); closeTags(true); }
  });
  window.addEventListener('resize', () => closeTags());
  document.getElementById('content').addEventListener('scroll', () => closeTags());

  function showTags(trigger, tags, label) {
    closeTags();
    const node = el('div', 'memory-tags-popover');
    node.id = trigger.id + '-popover';
    node.setAttribute('role', 'dialog'); node.setAttribute('aria-label', '全部' + label); node.tabIndex = -1;
    for (const tag of tags) {
      const chip = el('span', 'memory-chip', tag.dimension + ': ' + tag.value);
      chip.title = tag.value; node.append(chip);
    }
    document.body.append(node);
    const box = trigger.getBoundingClientRect();
    node.style.left = Math.max(8, Math.min(box.left, window.innerWidth - node.offsetWidth - 8)) + 'px';
    node.style.top = Math.max(8, Math.min(box.bottom + 6, window.innerHeight - node.offsetHeight - 8)) + 'px';
    popup = {node, trigger}; trigger.setAttribute('aria-expanded', 'true'); node.focus();
  }

  function row(memory, searchMode, index) {
    // Position-based IDs let callers identify regions of the current page.
    // dataset.memoryId continues to identify the underlying memory record.
    const rowId = 'memory-row-' + (index + 1);
    const title = clean(memory.summary) || deriveSummary(memory.text) || '空内容记忆';
    const body = clean(memory.text), prefix = title.replace(/…$/, '');
    const remainder = body.startsWith(prefix) ? body.slice(prefix.length).trim() : body;
    const preview = Array.from(remainder).slice(0, 256).join('');
    const item = el('div', 'memory-row');
    item.id = rowId;
    item.dataset.memoryId = memory.id;
    const open = el('button', 'memory-open'); open.type = 'button';
    open.setAttribute('aria-label', '查看记忆：' + title);
    open.title = title;
    open.onclick = () => onOpen(memory.id);
    const icon = el('span', 'memory-row-icon', '▤'); icon.setAttribute('aria-hidden', 'true');
    const content = el('div', 'memory-row-content');
    const heading = el('div', 'memory-row-title', title); heading.title = title;
    const summary = el('div', 'memory-row-summary', preview || '\u00a0');
    if (!preview) summary.setAttribute('aria-hidden', 'true');
    content.append(heading, summary);
    const footer = el('div', 'memory-row-footer');
    const metadata = el('div', 'memory-row-metadata');
    if (searchMode) {
      const label = ({semantic: 'Semantic', lexical: 'FTS', anchor: 'Anchor'})[searchMode];
      const score = el('span', 'memory-chip memory-search-score',
        label + ' ' + memory.score.toFixed(3));
      score.title = label + ' ' + String(memory.score);
      score.id = rowId + '-score';
      metadata.append(score);
    }
    const namespace = el('div', 'memory-tag-group memory-namespace'); namespace.title = memory.namespace || '';
    namespace.append(el('span', 'memory-group-label', '空间：'), el('span', 'memory-chip memory-namespace-value', memory.namespace || '—'));
    metadata.append(namespace);
    for (const [field, dimension, label, limit] of [['categories', 'category', '分类', 3], ['keywords', 'keyword', '关键词', 5]]) {
      const tags = values(memory.metadata?.[field]).map(value => ({dimension, value}));
      if (!tags.length) continue;
      const group = el('div', 'memory-tag-group'); group.id = rowId + '-' + field;
      group.append(el('span', 'memory-group-label', label + '：'));
      tags.slice(0, limit).forEach((tag, tagIndex) => {
        const chip = el('span', 'memory-chip memory-' + dimension + '-chip', tag.value);
        chip.title = label + ': ' + tag.value;
        chip.id = rowId + '-' + dimension + '-' + (tagIndex + 1);
        group.append(chip);
      });
      if (tags.length > limit) {
        const overflow = el('button', 'memory-chip memory-tags-more', '+' + (tags.length - limit));
        overflow.id = rowId + '-' + dimension + '-more';
        overflow.type = 'button'; overflow.setAttribute('aria-label', '查看全部 ' + tags.length + ' 个' + label);
        overflow.setAttribute('aria-haspopup', 'dialog'); overflow.setAttribute('aria-expanded', 'false');
        overflow.onclick = () => showTags(overflow, tags, label); group.append(overflow);
      }
      metadata.append(group);
    }
    const stamp = memoryDate(memory), time = el('span', 'memory-row-time', displayDate(stamp));
    time.title = stamp ? formatTime(stamp) : '无有效时间';
    for (const [region, node] of Object.entries({open, icon, content, title: heading, summary,
      footer, metadata, namespace, date: time})) node.id = rowId + '-' + region;
    if (stamp) open.setAttribute('aria-label', '查看记忆：' + title + '，更新于 ' + formatTime(stamp));
    footer.append(metadata, time); content.append(footer);
    const detailArrow = el('button', 'memory-row-detail-arrow', '›');
    detailArrow.id = rowId + '-detail-arrow';
    detailArrow.type = 'button'; detailArrow.dataset.debugNavigation = '';
    detailArrow.setAttribute('aria-label', '查看记忆明细：' + title);
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
    closeTags(); root.replaceChildren(); pagination.replaceChildren();
    root.setAttribute('aria-busy', String(state.status === 'loading'));
    const searching = Boolean(state.submittedQuery);
    const resolution = document.getElementById('memories-filter-resolution'); resolution.replaceChildren();
    if (searching && state.searchMode === 'semantic' && state.status === 'ready' && state.filterResolution) {
      for (const [dimension, label] of [['keywords', '关键词'], ['categories', '分类']]) {
        for (const mapping of state.filterResolution[dimension] || []) {
          if (mapping.match_type === 'exact') continue;
          const text = mapping.match_type === 'none' ? label + ' ' + mapping.input + ' 未找到匹配值'
            : label + '：' + mapping.input + ' → ' + mapping.matches.filter(match => match.accepted).map(match => match.value).join('、');
          resolution.append(el('div', '', text));
        }
      }
    }
    count.textContent = state.status === 'ready' ? (searching ? '本次搜索 ' + state.total + ' 条 · 最多 ' + state.k + ' 条' : state.total + ' memories') : state.status === 'error' ? '加载失败' : 'Loading...';
    if (state.status === 'loading' || state.status === 'idle') {
      const skeleton = el('div', 'memory-skeletons'); skeleton.setAttribute('aria-label', '正在加载记忆');
      for (let i = 0; i < 5; i++) skeleton.append(el('div', 'memory-skeleton'));
      root.append(skeleton);
    } else if (state.status === 'error') {
      const error = el('div', 'memory-state'); error.setAttribute('role', 'alert');
      error.append(el('p', '', 'Error: ' + state.error.message), button('Retry', onRetry));
      if (state.error.payload?.response_code === 'namespace_not_found') error.append(button('返回全部范围', onAll));
      root.append(error);
    } else if (!state.items.length) {
      const filtered = state.namespace || state.categories.length || state.keywords.length
        || state.updatedAfter || state.updatedBefore;
      const empty = el('div', 'memory-state');
      empty.append(el('p', '', filtered || searching ? '当前搜索或筛选没有匹配的记忆' : '暂无记忆'));
      if (filtered) empty.append(button('Clear filters', onReset));
      else if (searching) empty.append(button('清空搜索', onClearSearch));
      root.append(empty);
    } else state.items.forEach((memory, index) => root.append(row(memory, searching ? state.searchMode : null, index)));

    const ready = state.status === 'ready', last = Math.max(1, Math.ceil(state.total / state.pageSize));
    const start = ready && state.total ? (state.page - 1) * state.pageSize + 1 : 0;
    const end = ready ? Math.min(state.page * state.pageSize, state.total) : 0;
    pagination.append(el('span', 'memories-range', ready ? start + '–' + end + ' of ' + state.total + (searching ? ' 本次搜索结果' : ' memories') : ''));
    const nav = el('nav', 'memories-pages'); nav.setAttribute('aria-label', '记忆分页');
    if (ready && state.total) {
      nav.append(button('‹', () => onPage(state.page - 1), state.page === 1));
      nav.lastChild.setAttribute('aria-label', '上一页');
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
      nav.lastChild.setAttribute('aria-label', '下一页');
    }
    pagination.append(nav);
    const size = el('label', 'memories-size', 'Show '), select = el('select'); select.id = 'memories-page-size';
    for (const number of [10, 20, 50]) select.append(new Option(String(number), String(number)));
    select.value = state.pageSize;
    select.onchange = () => onPageSize(Number(select.value)); size.append(select); pagination.append(size);
  }
  return {render(state) { render(state); setSelected(selectedId); }, closeTags, setSelected};
}
