// core.js — DOM lookup, escaping and formatting helpers. No application state.
import { locale } from './i18n.js';

export const dockerMode = document.documentElement.dataset.deploymentMode === 'docker';

export function $(id) { return document.getElementById(id) }

export function formatTime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return isoStr;
  return d.toLocaleString(locale);
}

export function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function escapeHtml(value) {
  const node = document.createElement('span');
  node.textContent = String(value);
  return node.innerHTML;
}

export function esc(s) {
  if (s == null) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

export function truncateJson(obj, maxLen = 160) {
  let s;
  try { s = JSON.stringify(obj); } catch(e) { s = String(obj); }
  if (s.length > maxLen) return s.slice(0, maxLen) + '…';
  return s;
}

export function deriveSummary(text, maxChars = 80) {
  const lines = String(text || '').split(/\r?\n/)
    .map(line => line.trim().replace(/\s+/g, ' '))
    .filter(line => line && line !== '```' && line !== '~~~')
    .map(line => line.replace(/^(?:#{1,6}\s+|[-*+]\s+|\d+[.)、]\s*)/, '').trim())
    .filter(Boolean);
  if (!lines.length) return '';
  let candidate = lines[0];
  if (Array.from(candidate.replace(/[：:]$/, '')).length < 10 && lines.length > 1) {
    candidate += ' ' + lines[1];
  }
  const sentence = candidate.match(/^.*?[。！？!?；;]/);
  if (sentence && Array.from(sentence[0]).length <= maxChars) return sentence[0];
  const chars = Array.from(candidate);
  return chars.length <= maxChars ? candidate : chars.slice(0, maxChars).join('').trimEnd() + '…';
}
