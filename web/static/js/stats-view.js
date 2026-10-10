// Safe, shared Statistics rendering. Data comes from the metadata API only.
const labels = {categories: 'Categories', keywords: 'Keywords', anchors: 'Anchors'};
export const number = value => value.toLocaleString();
export const percent = value => value > 0 && value < .001 ? '<0.1%' : (value * 100).toFixed(1) + '%';
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(text, action, cls = '') {
  const node = el('button', cls, text); node.type = 'button'; node.onclick = action; return node;
}
function drilldownArrow() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'stats-drilldown-arrow'); svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', 'm9 6 6 6-6 6'); path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'currentColor'); path.setAttribute('stroke-width', '1.5');
  path.setAttribute('stroke-linecap', 'round'); path.setAttribute('stroke-linejoin', 'round');
  svg.append(path); return svg;
}
function panel(title, id) {
  const node = el('section', 'stats-panel'); node.id = id;
  const heading = el('h2', '', title); heading.id = id + '-title';
  node.setAttribute('aria-labelledby', heading.id); node.append(heading); return node;
}
function color(name) {
  let hash = 0; for (const char of name) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
  return 'var(--stats-color-' + (hash % 8) + ')';
}
function overview(data) {
  const node = panel('Overview', 'stats-overview'), grid = el('dl', 'stats-overview-grid');
  const metrics = [['Memories', data.total_memories], ['Namespaces', data.namespace_distribution.length],
    ...Object.entries(labels).map(([key, label]) => [label, data.dimensions[key].unique_count])];
  for (const [label, value] of metrics) {
    const item = el('div', 'stats-metric'); item.dataset.metric = label.toLowerCase();
    item.append(el('dt', '', label), el('dd', '', number(value))); grid.append(item);
  }
  node.append(grid); return node;
}
function distribution(data, selectNamespace) {
  const node = panel('Namespace Distribution', 'stats-distribution'), body = el('div', 'stats-distribution-body');
  const chart = el('div', 'stats-donut'), svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 200 200'); svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Namespace distribution: ' + number(data.total_memories) + ' memories. Exact counts are listed alongside.');
  const circle = () => {
    const c = document.createElementNS(svg.namespaceURI, 'circle');
    for (const [key, value] of Object.entries({cx: 100, cy: 100, r: 80, fill: 'none', 'stroke-width': 24, pathLength: 100}))c.setAttribute(key, value);
    return c;
  };
  const track = circle(); track.setAttribute('stroke', 'var(--border-subtle)'); svg.append(track);
  let offset = 0;
  for (const item of data.namespace_distribution) {
    const share = item.count / data.total_memories * 100, slice = circle();
    slice.setAttribute('stroke', color(item.name)); slice.setAttribute('stroke-dasharray', share + ' ' + (100-share));
    slice.setAttribute('stroke-dashoffset', -offset); slice.setAttribute('transform', 'rotate(-90 100 100)');
    svg.append(slice); offset += share;
  }
  const center = el('div', 'stats-donut-center'); center.setAttribute('aria-hidden', 'true');
  center.append(el('strong', '', number(data.total_memories)), el('span', '', 'Memories')); chart.append(svg, center);
  const list = el('div', 'stats-namespace-list');
  if (!data.namespace_distribution.length)list.append(el('p', 'stats-empty', 'No namespaces with memories.'));
  for (const item of data.namespace_distribution) {
    const row = button('', () => selectNamespace(item.name), 'stats-namespace-row'); row.dataset.namespace = item.name;
    const name = el('span', 'stats-namespace-name'), dot = el('span', 'stats-color-dot');
    dot.style.background = color(item.name); dot.setAttribute('aria-hidden', 'true'); name.append(dot, el('span', '', item.name));
    row.append(name, el('span', 'stats-count', number(item.count)), el('span', 'stats-percent', percent(item.count/data.total_memories)), drilldownArrow());
    row.setAttribute('aria-label', item.name + ', ' + item.count + ' memories, ' + percent(item.count/data.total_memories)); list.append(row);
  }
  body.append(chart, list); node.append(body); return node;
}
function dimensionPanel(key, data, state, onTop, onDrilldown) {
  const d = data.dimensions[key], title = labels[key], node = panel(title, 'stats-' + key);
  const header = el('div', 'stats-panel-heading'), controls = el('div', 'stats-top-controls');
  controls.setAttribute('role', 'group'); controls.setAttribute('aria-label', title + ' ranking size');
  header.append(node.firstChild, controls); node.append(header);
  for (const n of [5, 30, 100]) {
    const b = button('Top ' + n, () => onTop(key, n)); b.id = 'stats-' + key + '-top-' + n;
    b.setAttribute('aria-pressed', String(state.top[key] === n)); controls.append(b);
  }
  const summary = el('div', 'stats-dimension-summary'), unique = el('div'), coverage = el('div');
  unique.append(el('span', 'stats-label', 'Unique Count'), el('strong', '', number(d.unique_count)));
  coverage.append(el('span', 'stats-label', 'Coverage'), el('strong', '', percent(d.coverage_ratio)),
    el('span', 'stats-coverage-count', number(d.covered_memories) + ' / ' + number(data.total_memories) + ' memories'));
  summary.append(unique, coverage);
  const progress = el('progress', 'stats-coverage'); progress.max = 1; progress.value = d.coverage_ratio;
  progress.setAttribute('aria-label', title + ' coverage'); progress.setAttribute('aria-valuetext', d.covered_memories + ' of ' + data.total_memories + ' memories');
  summary.append(progress);
  const list = el('ol', 'stats-ranking'); list.id = 'stats-' + key + '-ranking';
  list.setAttribute('aria-label', title + ' ranked by memory count');
  if (state.top[key] !== 5)list.classList.add('stats-ranking-scroll');
  const items = d.top.slice(0, state.top[key]);
  if (!items.length)list.append(el('li', 'stats-empty', 'No ' + title.toLowerCase() + ' in this scope.'));
  items.forEach((item, index) => {
    const li = el('li'), ratio = data.total_memories ? item.count/data.total_memories : 0;
    const b = button('', () => onDrilldown(key, item.value), 'stats-rank-row'); b.dataset.value = item.value;
    b.setAttribute('aria-label', item.value + ', ' + item.count + ' memories, ' + percent(ratio));
    const text = el('span', 'stats-rank-name', item.value); text.title = item.value;
    const bar = el('span', 'stats-rank-bar'), fill = el('span'); fill.style.width = ratio * 100 + '%';
    bar.setAttribute('aria-hidden', 'true'); bar.append(fill);
    b.append(el('span', 'stats-rank-number', String(index+1)), text,
      el('span', 'stats-count', number(item.count)), el('span', 'stats-percent', percent(ratio)), drilldownArrow(), bar);
    li.append(b); list.append(li);
  });
  node.append(summary, list); return node;
}
export function renderStats(root, data, state, callbacks) {
  const panels = el('div', 'stats-dimensions');
  for (const key of Object.keys(labels))panels.append(dimensionPanel(key, data, state, callbacks.onTop, callbacks.onDrilldown));
  root.replaceChildren(overview(data), ...(data.scope.namespace === null ? [distribution(data, callbacks.onNamespace)] : []), panels);
}
export function renderStatsStatus(root, message, error, onRetry, onAll) {
  const node = el('div', 'stats-status', message); node.setAttribute('role', error ? 'alert' : 'status');
  if (error)node.append(button('Retry', onRetry, 'btn btn-outline'), button('All Namespaces', onAll, 'btn btn-outline'));
  root.replaceChildren(node);
}
