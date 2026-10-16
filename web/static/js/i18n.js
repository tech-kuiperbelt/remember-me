// i18n.js — current locale and translation lookup. Every module may import t()
// and locale. Messages come from the catalog node injected by the server on the
// first screen, so no separate locale request is made.
const node = document.getElementById('i18n-catalog');
let catalog = { locale: 'zh-CN', messages: {}, fallback: 'zh-CN' };
if (node) {
  try {
    const parsed = JSON.parse(node.textContent);
    if (parsed && typeof parsed === 'object') catalog = parsed;
  } catch (e) { /* keep the built-in default; the server always injects a catalog */ }
}

export const locale = catalog.locale || 'zh-CN';
const messages = catalog.messages || {};
const fallbackMessages = catalog.fallbackMessages || {};

export function t(key, params) {
  let value = messages[key];
  if (value == null) value = fallbackMessages[key];
  if (value == null) value = key;
  if (params) {
    value = value.replace(/\{(\w+)\}/g,
      (match, name) => (params[name] == null ? match : String(params[name])));
  }
  return value;
}

export function has(key) {
  return Object.prototype.hasOwnProperty.call(messages, key);
}
