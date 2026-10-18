import { node } from './detail-dom.js';
import { copyText } from './markdown.js';
import { toast } from './ui.js';
import { t } from './i18n.js';

function copyButton(id, label, value) {
  const button = node('button', id, undefined, 'detail-copy-icon');
  button.type = 'button'; button.title = label; button.setAttribute('aria-label', label);
  const icon = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/></svg>';
  button.innerHTML = icon;
  let generation = 0;
  button.onclick = async () => {
    const request = ++generation;
    try {
      await copyText(value);
      if (generation !== request) return;
      button.textContent = '✓'; button.setAttribute('aria-label', t('common.copied'));
      setTimeout(() => { if (generation === request) { button.innerHTML = icon; button.setAttribute('aria-label', label); } }, 1200);
    } catch (error) { toast(error.message, 'error'); }
  };
  return button;
}
function localTime(value) {
  const date = new Date(value);
  if (!value || !Number.isFinite(date.getTime())) return value || '—';
  const pad = number => String(number).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
export function renderTechnicalDetails(memory) {
  const details = node('details', 'detail-technical', undefined, 'detail-technical');
  const toggle = node('summary', 'detail-technical-toggle', t('detail.technical.toggle'));
  toggle.dataset.debugNavigation = ''; details.append(toggle);
  details.append(node('h3', 'detail-technical-basic-label', t('detail.technical.basic')));
  const list = node('dl', 'detail-technical-values');
  for (const [key, label, value] of [['id', t('detail.technical.id'), memory.id], ['created', t('detail.technical.created'), memory.created_at], ['updated', t('detail.technical.updated'), memory.updated_at]]) {
    const cell = node('dd', 'detail-' + key + '-cell');
    const text = node('span', 'detail-' + key + '-value', key === 'id' ? value : localTime(value));
    if (key !== 'id') text.title = value || '';
    cell.append(text);
    if (key === 'id') cell.append(copyButton('detail-copy-id', t('detail.technical.copyId'), memory.id));
    list.append(node('dt', 'detail-' + key + '-label', label), cell);
  }
  details.append(list, node('h3', 'detail-raw-metadata-label', t('detail.technical.rawMetadata')));
  const raw = JSON.stringify(memory.metadata || {}, null, 2);
  const box = node('div', 'detail-metadata-box', undefined, 'detail-metadata-box');
  const pre = node('pre', 'detail-metadata');
  const pattern = /"(?:\\.|[^"\\])*"\s*:|"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g;
  let offset = 0, index = 0;
  for (const match of raw.matchAll(pattern)) {
    pre.append(document.createTextNode(raw.slice(offset, match.index)));
    const value = match[0], kind = value.endsWith(':') ? 'key' : value.startsWith('"') ? 'string' : /^-?\d/.test(value) ? 'number' : 'literal';
    pre.append(node('span', 'detail-metadata-token-' + (++index), value, 'detail-json-' + kind));
    offset = match.index + value.length;
  }
  pre.append(document.createTextNode(raw.slice(offset)));
  box.append(pre, copyButton('detail-copy-metadata', t('detail.technical.copyMetadata'), raw));
  details.append(box);
  return details;
}
