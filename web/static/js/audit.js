// Debug-only audit viewer. Detail snapshots are the event returned by the API.
import { $, esc, formatTime } from './core.js';
import { api } from './api.js';
import { debugEnabled } from './ui.js';
import { getNamespaces, onNamespacesChanged } from './store.js';
import { createNamespacePicker } from './namespace-picker.js';
import { createDrawerAnimator } from './drawer-motion.js';
import { presentAudit } from './audit-presentation.js';
import { t } from './i18n.js';

let version = 0, events = [], eventKeys = [], recent = null, picker, opened = false, background, priorFocus;
const animate = createDrawerAnimator($('audit-modal'), $('audit-panel'));
function updateNamespaces() {
  picker?.update([...new Set([...getNamespaces(), ...events.map(ev => ev.namespace).filter(Boolean)])].sort());
}
export async function loadAudit() {
  if (!debugEnabled()) return;
  const request = ++version;
  let limit = parseInt($('audit-limit').value, 10);
  if (!Number.isFinite(limit) || limit < 1) limit = 200;
  const params = new URLSearchParams({limit: String(Math.min(5000, limit))});
  if ($('audit-tool').value) params.set('tool', $('audit-tool').value);
  if ($('audit-namespace').value.trim()) params.set('namespace', $('audit-namespace').value.trim());
  const list = $('audit-list');
  list.setAttribute('aria-busy', 'true');
  list.innerHTML = '<div class="empty-state">' + esc(t('audit.loading')) + '</div>';
  try {
    const data = await api('/audit?' + params);
    if (request !== version) return;
    events = data || []; updateNamespaces(); render();
  } catch (e) {
    if (request !== version) return;
    list.innerHTML = '<div class="empty-state">Error: ' + esc(e.message) + '</div>';
  } finally {
    if (request === version) list.setAttribute('aria-busy', 'false');
  }
}
// Audit has no event ID. Match the immutable event snapshot, not its list position.
function eventIdentity(event) {
  return JSON.stringify(event, (_key, value) => value && typeof value === 'object' && !Array.isArray(value)
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])) : value);
}
function markRecent(key) {
  if (key) recent = key;
  $('audit-list').querySelectorAll('.audit-row').forEach((row, i) => {
    const candidate = eventKeys[i];
    row.classList.toggle('is-recent', Boolean(recent && candidate && recent.identity === candidate.identity && recent.occurrence === candidate.occurrence));
  });
}
function render() {
  const list = $('audit-list');
  if (!events.length) { list.innerHTML = '<div class="empty-state">' + esc(t('audit.empty')) + '</div>'; return; }
  const occurrences = new Map();
  eventKeys = [];
  const rows = events.map((ev, i) => {
    const identity = eventIdentity(ev), occurrence = occurrences.get(identity) || 0;
    occurrences.set(identity, occurrence + 1);
    const key = {identity, occurrence};
    eventKeys.push(key);
    const id = 'audit-row-' + (i + 1), view = presentAudit(ev);
    const row = document.createElement('article');
    row.id = id; row.className = 'memory-row audit-row';
    const cell = (name, label, content) => {
      const el = document.createElement('div'), heading = document.createElement('span'), value = document.createElement('span');
      el.id = id + '-' + name; el.className = 'audit-cell audit-cell-' + name;
      heading.className = 'audit-cell-label'; heading.textContent = label;
      value.className = 'audit-cell-value'; value.textContent = content;
      el.append(heading, value); row.append(el);
      return value;
    };
    cell('time', t('audit.col.time'), formatTime(ev.ts) || '—');
    cell('action', t('audit.col.action'), ev.tool || '—');
    cell('namespace', t('audit.col.namespace'), view.namespace);
    cell('summary', t('audit.col.info'), view.summary);
    const status = cell('result', t('audit.col.result'), view.status);
    status.id = id + '-status';
    status.className = 'audit-row-status ' + (view.status === 'error' ? 'text-danger' : view.status === 'ok' ? 'text-success' : 'text-muted');
    cell('duration', t('audit.col.duration'), view.duration);
    const button = document.createElement('button'), arrow = document.createElement('span');
    button.id = id + '-detail'; button.className = 'audit-row-open'; button.type = 'button';
    button.setAttribute('aria-label', t('audit.detail.open', {tool: ev.tool || 'Audit'}));
    arrow.className = 'memory-row-detail-arrow'; arrow.setAttribute('aria-hidden', 'true'); arrow.textContent = '›';
    button.append(arrow); button.onclick = () => openDetail(ev, key); row.append(button);
    return row;
  });
  list.replaceChildren(...rows);
  markRecent();
}
function openDetail(event, key) {
  markRecent(key);
  picker.close(); priorFocus = document.activeElement;
  background = {inert: $('app-shell').inert, overflow: document.body.style.overflow};
  $('app-shell').inert = true; document.body.style.overflow = 'hidden';
  $('audit-json').textContent = JSON.stringify(event, null, 2);
  $('audit-detail-body').scrollTop = 0; $('audit-json').scrollLeft = 0;
  opened = true; animate(true); $('btn-audit-close').focus();
}
function closeDetail() {
  if (!opened) return;
  opened = false; animate(false);
  $('app-shell').inert = background.inert; document.body.style.overflow = background.overflow;
  if (priorFocus?.isConnected) priorFocus.focus();
}
export function hide() { ++version; picker?.close(); closeDetail(); }
export function mount() {
  picker = createNamespacePicker({control: $('audit-namespace-control'), input: $('audit-namespace'),
    idPrefix: 'audit-namespace', popupRoot: $('page-audit'),
    onChoose: value => { $('audit-namespace').value = value; loadAudit(); }});
  onNamespacesChanged(updateNamespaces); updateNamespaces();
  $('btn-audit-refresh').onclick = loadAudit;
  $('audit-tool').onchange = loadAudit;
  $('audit-namespace').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing) loadAudit(); });
  $('btn-audit-close').onclick = closeDetail;
  $('audit-modal').onclick = e => { if (e.target === e.currentTarget) closeDetail(); };
  document.addEventListener('keydown', e => {
    if (!opened || e.isComposing || e.defaultPrevented) return;
    if (e.key === 'Escape') { e.preventDefault(); closeDetail(); }
    if (e.key === 'Tab') {
      const first = $('btn-audit-close'), last = $('audit-json');
      if (!$('audit-panel').contains(document.activeElement) || (e.shiftKey && document.activeElement === first) || (!e.shiftKey && document.activeElement === last)) {
        e.preventDefault(); (e.shiftKey ? last : first).focus();
      }
    }
  });
}
