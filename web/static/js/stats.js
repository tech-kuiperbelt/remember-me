// Scoped Statistics state and lifecycle; global candidate refresh stays separate.
import { $ } from './core.js';
import { api } from './api.js';
import { getNamespaces, getDefaultNamespace, getCategoryState, onNamespacesChanged, onDefaultNamespaceChanged, onCategoriesChanged, onStatsChanged } from './store.js';
import { refreshStatsRegistry } from './stats-registry.js';
import { renderStats, renderStatsStatus } from './stats-view.js';
import { renderRetrieval, validateRetrieval, renderRetrievalLoading } from './stats-retrieval.js';
import { t } from './i18n.js';
const keys = ['categories', 'keywords', 'anchors'];
const state = {view: 'memory', days: 7, retrievalScroll: 0, namespace: null, top: {categories: 5, keywords: 5, anchors: 5}, scroll: {categories: 0, keywords: 0, anchors: 0}, pageScroll: 0};
let active = false, version = 0, data = null, controller = null, dispose = () => {}, drilldown = () => {};
function capture() {
  if (!data || !active) return;
  if (state.view === 'retrieval') { state.retrievalScroll = $('content').scrollTop; return; }
  state.pageScroll = $('content').scrollTop;
  for (const key of keys) { const list = $('stats-'+key+'-ranking'); if (list)state.scroll[key] = list.scrollTop; }
}
function restore() {
  if (state.view === 'memory')for (const key of keys) { const list = $('stats-'+key+'-ranking'); if (list)list.scrollTop = state.scroll[key]; }
  $('content').scrollTop = state.view === 'memory' ? state.pageScroll : state.retrievalScroll;
}
function updateOptions() {
  const select = $('stats-namespace'), defaultNS = getDefaultNamespace();
  const names = [...new Set([...getNamespaces(), ...(defaultNS.value ? [defaultNS.value] : []), ...(state.namespace === null ? [] : [state.namespace])])].sort();
  select.replaceChildren(new Option(t('stats.namespace.all'), ''), ...names.map(name => new Option(name, name)));
  select.value = state.namespace === null ? '' : state.namespace;
  const registry = getCategoryState();
  select.disabled = !registry.hasData && ['idle', 'loading'].includes(registry.status);
  $('stats-options-status').textContent = registry.status === 'error' || defaultNS.status === 'error' ? t('stats.optionsError') : '';
}
function controls() {
  for (const view of ['memory', 'retrieval']) {
    const tab = $('stats-tab-'+view), selected = view === state.view;
    tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
  }
  $('stats-days').hidden = state.view !== 'retrieval';
  for (const days of [7, 30, 90])$('stats-days-'+days).setAttribute('aria-pressed', String(days === state.days));
  $('stats-content').setAttribute('aria-labelledby', 'stats-tab-'+state.view);
}
function paint() {
  dispose(); dispose = () => {};
  if (state.view === 'retrieval') { dispose = renderRetrieval($('stats-content'), data); return; }
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
      !Array.isArray(s.namespace_distribution) || s.namespace_distribution.some(n => typeof n.name !== 'string' || !Number.isSafeInteger(n.count) || n.count <= 0))throw new Error(t('stats.invalid'));
  for (const key of keys) {
    const d = s.dimensions?.[key];
    if (!d || !Number.isSafeInteger(d.unique_count) || d.unique_count < 0 || !Number.isSafeInteger(d.covered_memories) || d.covered_memories < 0 || d.covered_memories > s.total_memories ||
        !Number.isFinite(d.coverage_ratio) || d.coverage_ratio < 0 || d.coverage_ratio > 1 || !Array.isArray(d.top) || d.top.length > 100 ||
        d.top.some(n => typeof n.value !== 'string' || !Number.isSafeInteger(n.count) || n.count <= 0 || n.count > s.total_memories))throw new Error(t('stats.invalid'));
  }
}
async function refresh(preserve = true) {
  if (!active) return;
  if (preserve)capture();
  controller?.abort(); controller = new AbortController();
  const request = ++version, scope = state.namespace, view = state.view, days = state.days;
  dispose(); dispose = () => {};
  data = null; $('stats-content').setAttribute('aria-busy', 'true');
  if (view === 'retrieval')renderRetrievalLoading($('stats-content'));
  else renderStatsStatus($('stats-content'), t('stats.loading'), false);
  try {
    const params = new URLSearchParams(); if (scope !== null)params.set('namespace', scope);
    let timezone;
    if (view === 'retrieval') {
      timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (!timezone)throw new Error(t('stats.timezone.unavailable'));
      params.set('days', days); params.set('timezone', timezone);
    }
    const s = await api('/stats/' + (view === 'memory' ? 'metadata' : 'retrieval') + (params.size ? '?' + params : ''), {signal: controller.signal});
    if (request !== version || !active) return;
    if (view === 'memory')validate(s, scope); else validateRetrieval(s, scope, days, timezone);
    data = s; updateOptions(); paint(); restore();
  } catch (error) {
    if (request !== version || !active) return;
    renderStatsStatus($('stats-content'), t('stats.loadFailed',{error:error.message}), true, () => refresh(false), () => selectNamespace(null));
  } finally {
    if (request === version && active) $('stats-content').setAttribute('aria-busy', 'false');
  }
}
function selectNamespace(namespace) {
  if (namespace === state.namespace) return;
  state.namespace = namespace; state.pageScroll = 0; state.retrievalScroll = 0; keys.forEach(key => state.scroll[key] = 0);
  updateOptions(); $('content').scrollTop = 0; refresh(false);
}
function selectView(view) {
  if (view === state.view) return;
  capture(); state.view = view; controls(); refresh(false);
}
// Compatibility entry for initialization, registry retry and global Refresh.
export function loadStats() {
  return Promise.all([refreshStatsRegistry(), active ? refresh() : Promise.resolve()]);
}
export function show() { active = true; controls(); updateOptions(); return refresh(false); }
export function hide() { capture(); active = false; ++version; controller?.abort(); dispose(); dispose = () => {}; }
export function mount({onDrilldown = () => {}} = {}) {
  drilldown = onDrilldown;
  $('stats-namespace').onchange = () => selectNamespace($('stats-namespace').value || null);
  for (const [index, view] of ['memory', 'retrieval'].entries()) {
    const tab = $('stats-tab-'+view);
    tab.onclick = () => selectView(view);
    tab.onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'memory' : event.key === 'End' ? 'retrieval' : ['memory', 'retrieval'][1-index];
      selectView(next); $('stats-tab-'+next).focus({preventScroll: true});
    };
  }
  for (const days of [7, 30, 90])$('stats-days-'+days).onclick = () => {
    if (state.days === days)return;
    state.days = days; state.retrievalScroll = 0; controls(); refresh(false);
  };
  onNamespacesChanged(updateOptions); onDefaultNamespaceChanged(updateOptions); onCategoriesChanged(updateOptions);
  controls(); updateOptions();
  onStatsChanged(loadStats);
}
