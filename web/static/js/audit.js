// audit.js — tool-call and background-task audit journal viewer.

import { $, esc, formatTime, truncateJson } from './core.js';
import { api } from './api.js';

export async function loadAudit() {
  const tool = $('audit-tool').value;
  const namespace = $('audit-namespace').value.trim();
  let limit = parseInt($('audit-limit').value, 10);
  if (isNaN(limit) || limit < 1) limit = 200;
  if (limit > 5000) limit = 5000;
  const params = new URLSearchParams({limit: String(limit)});
  if (tool) params.set('tool', tool);
  if (namespace) params.set('namespace', namespace);
  const tbody = $('audit-list');
  tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Loading...</td></tr>';
  try {
    const data = await api('/audit?' + params.toString());
    renderAudit(data);
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Error: ' + esc(e.message) + '</td></tr>';
  }
}

function renderAudit(events) {
  const tbody = $('audit-list');
  if (!events || events.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">暂无审计记录</td></tr>';
    return;
  }
  tbody.innerHTML = events.map(ev => {
    const status = ev.status === 'error'
      ? '<span class="text-danger" style="font-weight:600">error</span>'
      : '<span class="text-success">ok</span>';
    const err = ev.error ? ' <span class="text-danger" style="font-size:12px">' + esc(ev.error.type) + ': ' + esc(ev.error.message) + '</span>' : '';
    const params = truncateJson(ev.params);
    const result = truncateJson(ev.result);
    return '<tr style="border-bottom:1px solid var(--border-subtle);vertical-align:top">' +
      '<td class="text-muted" style="white-space:nowrap;font-size:12px">' + esc(formatTime(ev.ts)) + '</td>' +
      '<td>' + esc(ev.tool) + '</td>' +
      '<td>' + status + '</td>' +
      '<td class="text-muted">' + esc(ev.duration_ms) + '</td>' +
      '<td style="word-break:break-all;font-size:12px">' + esc(ev.namespace || '') + '</td>' +
      '<td style="font-size:12px;color:var(--text-secondary);word-break:break-all"><div>params: ' + esc(params) + '</div>' +
      '<div style="margin-top:2px">result: ' + esc(result) + '</div>' + err + '</td>' +
      '</tr>';
  }).join('');
}

export function mount() {
  $('btn-audit-refresh').addEventListener('click', loadAudit);
  $('audit-tool').addEventListener('change', loadAudit);
  $('audit-namespace').addEventListener('keydown', e => {
    if (e.key === 'Enter') loadAudit();
  });
}
