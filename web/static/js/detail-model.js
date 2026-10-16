// Pure draft rules; never rewrite the server-confirmed record.
import { t } from './i18n.js';
const whitespace = '[\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]';
const trimPattern = new RegExp('^' + whitespace + '+|' + whitespace + '+$', 'g');
export const trimValue = value => String(value ?? '').replace(trimPattern, '');
export const normalizeValues = values => [...new Set(values.filter(v => typeof v === 'string')
  .map(v => trimValue(v).toLowerCase()).filter(Boolean))];
export function normalizeAnchors(values) {
  const seen = new Set();
  return values.filter(v => typeof v === 'string').map(v => trimValue(v.normalize('NFKC'))
    .replace(new RegExp(whitespace + '+', 'g'), ' ')).filter(v => {
      const key = /^[\x00-\x7f]*$/.test(v) ? v.toLowerCase() : v;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
}
export function createDraft(memory) {
  const metadata = memory.metadata || {};
  return {namespace: memory.namespace || '', summary: memory.summary || '', text: memory.text || '',
    ...Object.fromEntries(['categories', 'keywords', 'anchors'].map(key => [key,
      Array.isArray(metadata[key]) ? [...metadata[key]] : []]))};
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
export function changedFields(memory, draft) {
  const before = createDraft(memory), changes = {};
  for (const key of ['namespace', 'summary', 'text']) {
    const normalize = key === 'text' ? v => v : trimValue;
    if (normalize(before[key]) !== normalize(draft[key])) changes[key] = true;
  }
  for (const key of ['categories', 'keywords', 'anchors']) {
    const normalize = key === 'anchors' ? normalizeAnchors : v => normalizeValues(v).sort();
    // Keep invalid historical entries visible and require an explicit repair.
    if (!same(normalize(before[key]), normalize(draft[key])) ||
        !same(before[key].filter(v => typeof v !== 'string' || !trimValue(v)), draft[key].filter(v => typeof v !== 'string' || !trimValue(v)))) changes[key] = true;
  }
  return changes;
}
export const isDirty = (memory, draft) => Object.keys(changedFields(memory, draft)).length > 0;
export function validateDraft(draft, memory) {
  const errors = {}, changes = memory ? changedFields(memory, draft) : {categories: true, keywords: true, anchors: true, text: true};
  const namespace = trimValue(draft.namespace);
  if (!namespace || Array.from(namespace).length > 64 || ['all', '*', 'none', 'null'].includes(namespace.toLowerCase())) errors.namespace = t('detail.error.namespace');
  for (const key of ['categories', 'keywords']) {
    if (changes[key] && draft[key].some(v => typeof v !== 'string' || !trimValue(v))) errors[key] = t('detail.error.tags');
  }
  if (changes.categories && normalizeValues(draft.categories).length > 3) errors.categories = t('detail.error.categoriesMax');
  if (changes.anchors || changes.text) {
    if (draft.anchors.length > 5 || draft.anchors.some(v => typeof v !== 'string') ||
        normalizeAnchors(draft.anchors).some(v => !v || Array.from(v).length > 128)) errors.anchors = t('detail.error.anchors');
  }
  return errors;
}
export function buildUpdatePayload(memory, draft) {
  const changes = changedFields(memory, draft), payload = {}, metadata = {};
  for (const key of ['text', 'namespace', 'summary']) if (changes[key]) payload[key] = key === 'text' ? draft[key] : trimValue(draft[key]);
  for (const key of ['categories', 'keywords']) if (changes[key]) metadata[key] = normalizeValues(draft[key]);
  if (changes.anchors || changes.text) metadata.anchors = normalizeAnchors(draft.anchors);
  if (changes.text) payload.summary = trimValue(draft.summary);
  if (Object.keys(metadata).length) payload.metadata = metadata;
  return payload;
}

export function createNewDraft(namespace = '') {
  return createDraft({namespace});
}
export function validateCreateDraft(draft) {
  const errors = validateDraft(draft, null);
  if (!trimValue(draft.text)) errors.text = t('detail.error.textRequired');
  return errors;
}
export function isCreateDirty(initial, draft) {
  return isDirty({namespace: initial.namespace, text: initial.text, summary: initial.summary,
    metadata: {categories: initial.categories, keywords: initial.keywords, anchors: initial.anchors}}, draft);
}
export function buildCreatePayload(draft) {
  const metadata = {};
  for (const key of ['categories','keywords']) if(draft[key].length) metadata[key] = normalizeValues(draft[key]);
  if(draft.anchors.length) metadata.anchors = normalizeAnchors(draft.anchors);
  const payload = {text: draft.text, namespace: trimValue(draft.namespace), metadata};
  if(trimValue(draft.summary)) payload.summary = trimValue(draft.summary);
  return payload;
}
