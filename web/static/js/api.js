// api.js — JSON fetch wrapper and namespace confirmation flow.
import { t } from './i18n.js';

export async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: {'Content-Type': 'application/json', ...opts.headers},
    ...opts,
  });
  if (!r.ok) {
    // Keep the full payload so callers can react to machine-readable codes
    // (e.g. namespace_not_found) instead of parsing the message.
    const payload = await r.json().catch(() => ({}));
    const err = new Error(payload.detail || r.statusText);
    err.status = r.status;
    err.payload = payload;
    err.retryAfter = r.headers.get('Retry-After');
    throw err;
  }
  return r.json();
}

// Namespace governance: an unknown namespace is rejected with
// response_code="namespace_not_found"; the operator confirms, then we retry with
// the matching confirmation code. Confirmation only bypasses existence — format
// and length checks still apply.
export function isNamespaceNotFound(e) {
  return !!(e && e.payload && e.payload.response_code === 'namespace_not_found');
}

export function confirmNamespaceCreation(e) {
  const meta = (e.payload && e.payload.response_meta) || {};
  const requested = meta.requested_namespace || t('api.namespace.requested');
  const known = (meta.known_namespaces || []).join(', ') || t('api.namespace.none');
  return confirm(t('api.namespace.confirm',{requested,known}));
}
