// Workspace state, network requests and local Top-K paging; no DOM dependencies.
import { api } from './api.js';

// ISO timestamps retain microseconds; timezone-free historical values mean UTC.
export function timestampMicros(value) {
  if (typeof value !== 'string') return null;
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})?$/);
  if (!match) return null;
  const [, year, month, day, hour, minute, second, fraction = '', zone = 'Z'] = match;
  const base = new Date(`${year}-${month}-${day}T${hour}:${minute}:${second}Z`);
  if (!Number.isFinite(base.getTime()) || base.getUTCFullYear() !== +year || base.getUTCMonth() + 1 !== +month
      || base.getUTCDate() !== +day || base.getUTCHours() !== +hour || base.getUTCMinutes() !== +minute
      || base.getUTCSeconds() !== +second) return null;
  let offset = 0;
  if (zone !== 'Z') {
    const hours = +zone.slice(1, 3), minutes = +zone.slice(4);
    if (hours > 23 || minutes > 59) return null;
    offset = (hours * 60 + minutes) * (zone[0] === '+' ? 1 : -1);
  }
  return BigInt(base.getTime() - offset * 60000) * 1000n + BigInt(fraction.padEnd(6, '0'));
}
const time = item => timestampMicros(item.updated_at) ?? timestampMicros(item.created_at);
function validateSearch(response) {
  if (!response || !Array.isArray(response.items) || response.items.some(item => !Number.isFinite(item.score))) {
    throw new Error('Invalid search response');
  }
  const resolution = response.filter_resolution;
  if (resolution !== null) {
    if (!resolution || typeof resolution !== 'object') throw new Error('Invalid filter resolution');
    for (const dimension of ['keywords', 'categories']) {
      if (!(dimension in resolution)) continue;
      if (!Array.isArray(resolution[dimension]) || resolution[dimension].some(item =>
        typeof item.input !== 'string' || !['exact', 'semantic', 'none'].includes(item.match_type)
        || !Array.isArray(item.matches) || item.matches.some(match => typeof match.value !== 'string'
          || typeof match.accepted !== 'boolean'))) throw new Error('Invalid filter resolution');
    }
  }
}

export function createMemoriesData(onChange) {
  const state = {inputQuery: '', submittedQuery: '', namespace: '', categories: [], keywords: [],
    browseSort: 'updated_at', searchSort: 'relevance', page: 1, pageSize: 20, k: 10, threshold: 0,
    updatedAfter: '', updatedBefore: '', items: [], total: 0, totalKind: 'exact', filterResolution: null,
    status: 'idle', error: null};
  let version = 0, collection = [];
  const searching = () => Boolean(state.submittedQuery);
  const snapshot = () => ({...state, sort: searching() ? state.searchSort : state.browseSort,
    categories: [...state.categories], keywords: [...state.keywords], items: [...state.items]});
  const emit = () => onChange(snapshot());
  function localPage() {
    let items = [...collection];
    if (state.searchSort === 'updated_at') items.sort((a, b) => {
      const first = time(a), second = time(b);
      return first === second ? 0 : first === null ? 1 : second === null ? -1 : first > second ? -1 : 1;
    });
    state.total = items.length;
    state.page = Math.min(state.page, Math.max(1, Math.ceil(items.length / state.pageSize)));
    state.items = items.slice((state.page - 1) * state.pageSize, state.page * state.pageSize);
  }
  async function refresh() {
    const request = ++version, query = snapshot();
    state.status = 'loading'; state.error = null; state.items = []; state.filterResolution = null;
    state.totalKind = searching() ? 'top_k_results' : 'exact'; collection = []; emit();
    try {
      if (query.submittedQuery) {
        const body = {query: query.submittedQuery, k: query.k, threshold: query.threshold, include_resolution: true};
        if (query.namespace) body.namespace = query.namespace;
        for (const dimension of ['categories', 'keywords']) if (query[dimension].length) body[dimension] = query[dimension];
        const response = await api('/memories/search', {method: 'POST', body: JSON.stringify(body)});
        if (request !== version) return;
        validateSearch(response); collection = response.items; state.filterResolution = response.filter_resolution;
        localPage();
      } else {
        let response;
        for (let correction = 0; correction < 2; correction++) {
          const params = new URLSearchParams({limit: query.pageSize, offset: (query.page - 1) * query.pageSize,
            sort_by: query.browseSort});
          if (query.namespace) params.set('namespace', query.namespace);
          if (query.updatedAfter) params.set('updated_after', query.updatedAfter);
          if (query.updatedBefore) params.set('updated_before', query.updatedBefore);
          for (const dimension of ['categories', 'keywords']) query[dimension].forEach(value => params.append(dimension, value));
          response = await api('/memories/page?' + params);
          if (request !== version) return;
          if (!Array.isArray(response.items) || !Number.isSafeInteger(response.total) || response.total < 0) {
            throw new Error('Invalid memory page response');
          }
          const last = Math.max(1, Math.ceil(response.total / query.pageSize));
          if (query.page <= last) break;
          if (correction) throw new Error('Memories changed while paging. Please retry.');
          query.page = last;
        }
        state.page = query.page; state.items = response.items; state.total = response.total;
      }
      state.status = 'ready'; emit();
    } catch (error) {
      if (request !== version) return;
      state.status = 'error'; state.error = error; emit();
    }
  }
  function assign(changes, resetPage = true) {
    for (const dimension of ['categories', 'keywords']) {
      if (dimension in changes) changes = {...changes, [dimension]:
        [...new Set(changes[dimension].map(value => value.trim().toLowerCase()).filter(Boolean))]};
      if (changes[dimension]?.length > 20) throw new Error('最多选择 20 项');
    }
    if ('sort' in changes) {
      changes = {...changes, [searching() ? 'searchSort' : 'browseSort']: changes.sort}; delete changes.sort;
    }
    Object.assign(state, changes);
    if (resetPage) state.page = 1;
  }
  function change(changes, resetPage = true) {
    const local = searching() && Object.keys(changes).every(key => ['sort', 'pageSize', 'page'].includes(key));
    assign(changes, resetPage);
    if (local) { if (state.status === 'ready') localPage(); emit(); return Promise.resolve(); }
    return refresh();
  }
  function clearSearch() {
    const reload = searching() || state.page !== 1;
    state.inputQuery = ''; state.submittedQuery = ''; state.page = 1;
    if (reload) return refresh();
  }
  function input(value) {
    state.inputQuery = value;
    if (!value.trim()) return clearSearch();
  }
  const submit = options => change({submittedQuery: state.inputQuery.trim(), ...options});
  function resetFilters() {
    if (!state.namespace && !state.categories.length && !state.keywords.length && !state.updatedAfter && !state.updatedBefore) return;
    return change({namespace: '', categories: [], keywords: [], updatedAfter: '', updatedBefore: ''});
  }
  function prepareKeywordBrowse(keyword, namespace) {
    ++version; collection = [];
    assign({inputQuery: '', submittedQuery: '', namespace: namespace || '', categories: [], keywords: [keyword],
      updatedAfter: '', updatedBefore: '', items: [], total: 0, status: 'idle', error: null,
      filterResolution: null, totalKind: 'exact'}); emit();
  }
  return {snapshot, refresh, change, resetFilters, input, submit, clearSearch, prepareKeywordBrowse};
}
