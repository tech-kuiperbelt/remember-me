// Workspace data and request ownership; no DOM dependencies.
import { api } from './api.js';

export function createMemoriesData(onChange) {
  const state = {namespace: '', sort: 'updated_at', page: 1, pageSize: 20,
    items: [], total: 0, totalKind: 'exact', status: 'idle', error: null};
  let version = 0;
  const snapshot = () => ({...state, items: [...state.items]});
  const emit = () => onChange(snapshot());

  async function refresh() {
    const request = ++version;
    const query = snapshot();
    state.status = 'loading'; state.error = null; state.items = []; emit();
    try {
      let data;
      for (let correction = 0; correction < 2; correction++) {
        const params = new URLSearchParams({limit: query.pageSize,
          offset: (query.page - 1) * query.pageSize, sort_by: query.sort});
        if (query.namespace) params.set('namespace', query.namespace);
        data = await api('/memories/page?' + params);
        if (request !== version) return;
        if (!Array.isArray(data.items) || !Number.isSafeInteger(data.total) || data.total < 0) {
          throw new Error('Invalid memory page response');
        }
        const last = Math.max(1, Math.ceil(data.total / query.pageSize));
        if (query.page <= last) break;
        if (correction) throw new Error('Memories changed while paging. Please retry.');
        query.page = last;
      }
      state.page = query.page; state.items = data.items; state.total = data.total;
      state.status = 'ready'; emit();
    } catch (error) {
      if (request !== version) return;
      state.page = query.page; state.status = 'error'; state.error = error; emit();
    }
  }

  function change(changes, resetPage = true) {
    Object.assign(state, changes);
    if (resetPage) state.page = 1;
    return refresh();
  }
  return {snapshot, refresh, change};
}
