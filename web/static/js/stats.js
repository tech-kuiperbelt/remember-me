// Scoped Statistics state and lifecycle; global candidate refresh stays separate.
import { $ } from './core.js';
import { api } from './api.js';
import { onStatsChanged } from './store.js';
import { refreshStatsRegistry } from './stats-registry.js';
import { renderStats, renderStatsStatus } from './stats-view.js';
const keys = ['categories', 'keywords', 'anchors'];
const state = {namespace: null, top: {categories: 5, keywords: 5, anchors: 5}, scroll: {categories: 0, keywords: 0, anchors: 0}, pageScroll: 0};
let active = false, version = 0, data = null, options = [], drilldown = () => {};
function capture() {
  if (!data || !active) return;
  state.pageScroll = $('content').scrollTop;
  for (const key of keys) { const list = $('stats-'+key+'-ranking'); if (list)state.scroll[key] = list.scrollTop; }
}
function restore() {
  for (const key of keys) { const list = $('stats-'+key+'-ranking'); if (list)list.scrollTop = state.scroll[key]; }
  $('content').scrollTop = state.pageScroll;
}
function updateOptions() {
  const select = $('stats-namespace');
  const names = [...new Set([...options, ...(state.namespace === null ? [] : [state.namespace])])].sort();
  select.replaceChildren(new Option('All Namespaces', ''), ...names.map(name => new Option(name, name)));
  select.value = state.namespace === null ? '' : state.namespace;
}
function paint() {
  renderStats($('stats-content'), data, state, {
    onNamespace(namespace) { capture(); drilldown('namespace', namespace, namespace); },
    onTop(key, value) {
      capture(); state.top[key] = value; state.scroll[key] = 0; paint(); restore();
      $('stats-'+key+'-top-'+value).focus({preventScroll: true});
    },
    onDrilldown(key, value) { capture(); drilldown(key, value, state.namespace); },
  });
}
function validate(s, scope) {
  if (!s || s.scope?.namespace !== scope || !Number.isSafeInteger(s.total_memories) || s.total_memories < 0 ||
      !Array.isArray(s.namespace_options) || s.namespace_options.some(n => typeof n !== 'string' || !n.trim()) ||
      !Array.isArray(s.namespace_distribution) || s.namespace_distribution.some(n => typeof n.name !== 'string' || !Number.isSafeInteger(n.count) || n.count <= 0))throw new Error('Invalid statistics response');
  for (const key of keys) {
    const d = s.dimensions?.[key];
    if (!d || !Number.isSafeInteger(d.unique_count) || d.unique_count < 0 || !Number.isSafeInteger(d.covered_memories) || d.covered_memories < 0 || d.covered_memories > s.total_memories ||
        !Number.isFinite(d.coverage_ratio) || d.coverage_ratio < 0 || d.coverage_ratio > 1 || !Array.isArray(d.top) || d.top.length > 100 ||
        d.top.some(n => typeof n.value !== 'string' || !Number.isSafeInteger(n.count) || n.count <= 0 || n.count > s.total_memories))throw new Error('Invalid statistics response');
  }
}
async function refresh(preserve = true) {
  if (!active) return;
  if (preserve)capture();
  const request = ++version, scope = state.namespace;
  data = null; $('stats-content').setAttribute('aria-busy', 'true'); $('btn-stats-refresh').disabled = true;
  renderStatsStatus($('stats-content'), 'Loading statistics…', false);
  try {
    const params = new URLSearchParams(); if (scope !== null)params.set('namespace', scope);
    const s = await api('/stats/metadata' + (params.size ? '?' + params : ''));
    if (request !== version || !active) return;
    validate(s, scope); data = s; options = s.namespace_options; updateOptions(); paint(); restore();
  } catch (error) {
    if (request !== version || !active) return;
    const known = error.payload?.response_meta?.known_namespaces;
    if (Array.isArray(known)) { options = known; updateOptions(); }
    renderStatsStatus($('stats-content'), 'Unable to load statistics: ' + error.message, true, () => refresh(false), () => selectNamespace(null));
  } finally {
    if (request === version && active) { $('stats-content').setAttribute('aria-busy', 'false'); $('btn-stats-refresh').disabled = false; }
  }
}
function selectNamespace(namespace) {
  if (namespace === state.namespace) return;
  state.namespace = namespace; state.pageScroll = 0; keys.forEach(key => state.scroll[key] = 0);
  updateOptions(); $('content').scrollTop = 0; refresh(false);
}
// Compatibility entry for initialization, registry retry and global Refresh.
export function loadStats() {
  return Promise.all([refreshStatsRegistry(), active ? refresh() : Promise.resolve()]);
}
export function show() { active = true; updateOptions(); return refresh(false); }
export function hide() { capture(); active = false; ++version; }
export function mount({onDrilldown = () => {}} = {}) {
  drilldown = onDrilldown;
  $('stats-namespace').onchange = () => selectNamespace($('stats-namespace').value || null);
  $('btn-stats-refresh').onclick = () => refresh();
  onStatsChanged(loadStats);
}
