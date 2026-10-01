// stats.js — storage statistics page.

import { $, escapeHtml, formatBytes } from './core.js';
import { api } from './api.js';
import { showError } from './ui.js';
import { setNamespaces, setCategories, onStatsChanged } from './store.js';
import { switchPage } from './router.js';

export async function loadStats() {
  $('stats-content').innerHTML = '<div class="loading">Loading...</div>';
  try {
    const s = await api('/stats');
    const compact = s.ndjson_compact;
    const integrity = s.replica_integrity || {};
    const automaticHealth = s.ndjson_health_check;
    const embeddingStorage = s.embedding_storage || {};
    const embeddingProfiles = s.embedding_profile || {};
    const activeProfile = embeddingProfiles.active || s.embed_profile || '-';
    const configuredProfile = s.embed_profile || '-';
    const profileState = activeProfile === configuredProfile ? 'active/configured' : 'active differs from configured';
    const profileHtml = Array.isArray(embeddingProfiles.profiles) && embeddingProfiles.profiles.length ? `
      <div class="detail-field"><div class="label">Embedding Profiles</div><div class="value">${embeddingProfiles.profiles.map(p =>
        `<span class="badge" style="margin:2px">${escapeHtml(p.status)} · ${escapeHtml(p.provider)}/${escapeHtml(p.model)} · ${escapeHtml(p.dimension)}d · ${escapeHtml(p.dtype || '-')} · ${escapeHtml(p.vector_count || 0)} vectors</span>`
      ).join(' ')}</div></div>
    ` : '';
    const ollamaHtml = s.embed_provider === 'ollama' ? `
      <div class="detail-field"><div class="label">Ollama URL</div><div class="value">${escapeHtml(s.ollama_base_url || '-')}</div></div>
    ` : '';
    const compactHtml = compact ? `
      <div class="detail-field"><div class="label">NDJSON Compact</div><div class="value">${compact.last_result || 'not yet checked'}</div></div>
      <div class="detail-field"><div class="label">Last Check</div><div class="value">${compact.last_check_at || '-'}</div></div>
      <div class="detail-field"><div class="label">Last Compact</div><div class="value">${compact.last_compact_at || '-'}</div></div>
      <div class="detail-field"><div class="label">Last Reclaimed</div><div class="value">${formatBytes(compact.last_reclaimed_bytes || 0)} (${((compact.last_reclaimable_ratio || 0) * 100).toFixed(1)}%)</div></div>
      <div class="detail-field"><div class="label">Next Check Due</div><div class="value">${compact.next_due_at || '-'}</div></div>
      ${compact.last_error ? `<div class="detail-field"><div class="label">Compact Error</div><div class="value">${escapeHtml(compact.last_error)}</div></div>` : ''}
    ` : '';
    const replicaHealthHtml = s.backend === 'dual' ? `
      <div class="detail-field"><div class="label">Replica Health</div><div class="value"><span class="badge ${s.in_sync ? 'badge-success' : 'badge-danger'}">${s.in_sync ? 'IN SYNC' : 'OUT OF SYNC'}</span></div></div>
      ${!s.in_sync ? '<div class="detail-field"><div class="label">Recovery Action</div><div class="value text-danger">Run Full Export below to perform a verified Full Sync. The service will not repair replica drift automatically.</div></div>' : ''}
      <div class="detail-field"><div class="label">Pending</div><div class="value">${escapeHtml(s.pending ?? '-')}</div></div>
      <div class="detail-field"><div class="label">Replica Integrity</div><div class="value">Missing ${escapeHtml(integrity.missing ?? '-')} · Extra ${escapeHtml(integrity.extra ?? '-')} · Corrupt Lines ${escapeHtml(integrity.corrupt_lines ?? '-')}</div></div>
      <div class="detail-field"><div class="label">Live Check</div><div class="value">${escapeHtml(integrity.checked_at || '-')}</div></div>
      ${integrity.error ? `<div class="detail-field"><div class="label">Health Error</div><div class="value text-danger">${escapeHtml(integrity.error)}</div></div>` : ''}
      ${automaticHealth ? `
        <div class="detail-field"><div class="label">Last Automatic Check</div><div class="value">${escapeHtml(automaticHealth.last_result || '-')} · ${escapeHtml(automaticHealth.last_check_at || '-')} · trigger ${escapeHtml(automaticHealth.last_trigger || '-')}</div></div>
        <div class="detail-field"><div class="label">Next Automatic Check</div><div class="value">${escapeHtml(automaticHealth.next_due_at || '-')}</div></div>
        ${automaticHealth.last_error ? `<div class="detail-field"><div class="label">Automatic Check Error</div><div class="value text-danger">${escapeHtml(automaticHealth.last_error)}</div></div>` : ''}
      ` : '<div class="detail-field"><div class="label">Automatic Check</div><div class="value">Not run yet</div></div>'}
    ` : '';
    $('stats-content').innerHTML = `
      <div class="detail-field"><div class="label">Total Memories</div><div class="value" style="font-size:28px;font-weight:700">${s.total}</div></div>
      <div class="detail-field"><div class="label">Backend</div><div class="value">${s.backend}</div></div>
      <div class="detail-field"><div class="label">Storage Path</div><div class="value">${s.path}</div></div>
      ${replicaHealthHtml}
      <div class="detail-field"><div class="label">Embedding Provider</div><div class="value">${escapeHtml(s.embed_provider || '-')}</div></div>
      <div class="detail-field"><div class="label">Embedding Model</div><div class="value">${escapeHtml(s.embed_model || '-')}</div></div>
      <div class="detail-field"><div class="label">Embedding Shape</div><div class="value">${escapeHtml(s.embed_dimension || '-')}d · ${escapeHtml(s.embed_dtype || embeddingStorage.write_dtype || '-')}</div></div>
      <div class="detail-field"><div class="label">Active Profile</div><div class="value">${escapeHtml(activeProfile)} · ${escapeHtml(profileState)}</div></div>
      <div class="detail-field"><div class="label">Active Embedding Storage</div><div class="value">${embeddingStorage.count || 0} vectors · ${formatBytes(embeddingStorage.bytes || 0)} · ${escapeHtml(Object.entries(embeddingStorage.formats || {}).map(([format, count]) => `${format} × ${count}`).join(', ') || '-')}</div></div>
      ${profileHtml}
      ${ollamaHtml}
      <div class="detail-field"><div class="label">Namespaces (${Object.keys(s.namespace_counts || {}).length})</div><div class="value">${renderNamespaceCounts(s.namespace_counts)}</div></div>
      <div class="detail-field"><div class="label">Keywords (${s.keyword_total || 0})</div><div class="value"><button class="btn btn-outline" data-goto="keywords">Browse keywords</button></div></div>
      ${compactHtml}
      <div style="margin-top:16px">
        <button id="btn-stats-refresh" class="btn btn-sm btn-outline">Refresh</button>
      </div>
    `;
    const browse = $('stats-content').querySelector('[data-goto="keywords"]');
    if (browse) browse.addEventListener('click', () => switchPage('keywords'));
    $('btn-stats-refresh').addEventListener('click', loadStats);
    // Update the shared namespace/category registries; pages react through store subscriptions.
    setNamespaces(s.namespaces || [], s.namespace_counts || {});
    setCategories(s.category_counts || {});
  } catch(e) {
    showError('stats-content', e);
  }
}

function renderNamespaceCounts(counts) {
  if (!counts || !Object.keys(counts).length) return '<span class="text-muted">(none)</span>';
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return Object.entries(counts).map(([ns, n]) =>
    '<span class="badge ns" style="margin:2px">' + escapeHtml(ns) + ' · ' + n + '</span>'
  ).join(' ') + `<span class="text-muted" style="font-size:12px;margin-left:8px">total ${total}</span>`;
}

export function mount() {
  onStatsChanged(loadStats);
}
