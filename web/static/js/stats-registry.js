// Global candidate registry loading, independent of the scoped Statistics view.
import { api } from './api.js';
import { setNamespaces, setCategories, setCategoryState, setDefaultNamespace } from './store.js';
import { t } from './i18n.js';
let version = 0;
export async function refreshStatsRegistry() {
  const request = ++version;
  setCategoryState('loading'); setDefaultNamespace('loading');
  try {
    const s = await api('/stats');
    if (request !== version) return;
    setNamespaces(s.namespaces || [], s.namespace_counts || {});
    setCategories(s.category_counts || {});
    const valid = typeof s.default_namespace === 'string' && Boolean(s.default_namespace.trim());
    setDefaultNamespace(valid ? 'ready' : 'error', valid ? s.default_namespace : null,
      valid ? null : new Error(t('stats.defaultNamespace.failed')));
  } catch (error) {
    if (request !== version) return;
    setCategoryState('error', error); setDefaultNamespace('error', null, error);
  }
}
