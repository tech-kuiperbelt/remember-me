// Workspace state, network requests and local Top-K paging; no DOM dependencies.
import { t } from './i18n.js';
import { api } from './api.js';

function validateSearch(response) {
  if (!response || !Array.isArray(response.items) || response.items.some(item => !Number.isFinite(item.score))) {
    throw new Error(t('memories.error.invalidSearch'));
  }
  const resolution = response.filter_resolution;
  if (resolution !== null) {
    if (!resolution || typeof resolution !== 'object') throw new Error(t('memories.error.invalidResolution'));
    for (const dimension of ['keywords', 'categories']) {
      if (!(dimension in resolution)) continue;
      if (!Array.isArray(resolution[dimension]) || resolution[dimension].some(item =>
        typeof item.input !== 'string' || !['exact', 'semantic', 'none'].includes(item.match_type)
        || !Array.isArray(item.matches) || item.matches.some(match => typeof match.value !== 'string'
          || typeof match.accepted !== 'boolean'))) throw new Error(t('memories.error.invalidResolution'));
    }
  }
}

export function createMemoriesData(onChange) {
  const state = {inputQuery: '', submittedQuery: '', namespace: '', categories: [], keywords: [],
    browseSort: 'updated_at', searchMode: 'semantic', page: 1, pageSize: 20,
    searchParams: {semantic: {k: 10, threshold: 0}, lexical: {k: 10, minScore: null}, anchor: {k: 10, minScore: null}},
    updatedAfter: '', updatedBefore: '', items: [], total: 0, totalKind: 'exact', filterResolution: null,
    status: 'idle', error: null};
  let version = 0, collection = [];
  const searching = () => Boolean(state.submittedQuery);
  const snapshot = () => ({...state, sort: searching() ? 'relevance' : state.browseSort,
    ...state.searchParams[state.searchMode],
    searchParams: Object.fromEntries(Object.entries(state.searchParams).map(([mode, params]) => [mode, {...params}])),
    categories: [...state.categories], keywords: [...state.keywords], items: [...state.items]});
  const emit = () => onChange(snapshot());
  function localPage() {
    const items = collection;
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
        const body = {query: query.submittedQuery, k: query.k};
        if (query.searchMode === 'semantic') Object.assign(body, {threshold: query.threshold, include_resolution: true});
        else if (query.minScore !== null) body.min_score = query.minScore;
        if (query.namespace) body.namespace = query.namespace;
        for (const dimension of ['categories', 'keywords']) if (query[dimension].length) body[dimension] = query[dimension];
        if (query.updatedAfter) body.updated_after = query.updatedAfter;
        if (query.updatedBefore) body.updated_before = query.updatedBefore;
        const path = '/memories/search' + (query.searchMode === 'semantic' ? '' : '/' + query.searchMode);
        const raw = await api(path, {method: 'POST', body: JSON.stringify(body)});
        if (request !== version) return;
        const response = query.searchMode === 'semantic' ? raw : {items: raw, filter_resolution: null};
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
            throw new Error(t('memories.error.invalidPage'));
          }
          const last = Math.max(1, Math.ceil(response.total / query.pageSize));
          if (query.page <= last) break;
          if (correction) throw new Error(t('memories.error.paging'));
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
      if (changes[dimension]?.length > 20) throw new Error(t('memories.error.selectMax'));
    }
    if ('sort' in changes) {
      if (!searching()) state.browseSort = changes.sort;
      changes = {...changes}; delete changes.sort;
    }
    changes = {...changes};
    for (const key of ['k', 'threshold', 'minScore']) {
      if (key in changes) { state.searchParams[state.searchMode][key] = changes[key]; delete changes[key]; }
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
  function setMode(mode) {
    if (mode === state.searchMode || !Object.hasOwn(state.searchParams, mode)) return;
    state.searchMode = mode;
    if (searching()) { state.page = 1; return refresh(); }
    emit();
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
  function prepareMetadataDrilldown(dimension, value, namespace) {
    if (!['namespace', 'categories', 'keywords', 'anchors'].includes(dimension)) return;
    if (dimension === 'keywords') { prepareKeywordBrowse(value, namespace); return; }
    ++version; collection = [];
    const anchor = dimension === 'anchors';
    if (anchor) state.searchParams.anchor = {k: 10, minScore: null};
    assign({inputQuery: anchor ? value : '', submittedQuery: anchor ? value : '', namespace: namespace || '',
      categories: dimension === 'categories' ? [value] : [], keywords: [], updatedAfter: '', updatedBefore: '',
      ...(anchor ? {searchMode: 'anchor'} : {}), items: [], total: 0, status: 'idle', error: null,
      filterResolution: null, totalKind: anchor ? 'top_k_results' : 'exact'}); emit();
  }
  return {snapshot, refresh, change, setMode, resetFilters, input, submit, clearSearch, prepareKeywordBrowse, prepareMetadataDrilldown};
}
