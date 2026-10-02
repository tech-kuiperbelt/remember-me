// Search form and unapplied advanced field drafts; no API calls.
import { $ } from './core.js';

export function createMemoriesSearch({onInput, onSubmit, onClear, onApply}) {
  let searching = false, composing = false;
  const query = $('memories-query');
  const ids = ['memories-k', 'memories-threshold', 'memories-updated-after', 'memories-updated-before'];
  function error(id, message) {
    const field = $(id), output = $(id + '-error');
    field.setAttribute('aria-invalid', String(Boolean(message)));
    field.setAttribute('aria-describedby', output.id); output.textContent = message;
  }
  function validate(search) {
    ids.forEach(id => error(id, ''));
    const errors = [];
    const invalid = (id, message) => { error(id, message); errors.push(id); };
    let options;
    if (search) {
      const raw = $('memories-k').value.trim(), k = Number(raw), thresholdRaw = $('memories-threshold').value.trim();
      const threshold = Number(thresholdRaw);
      if (!/^\d+$/.test(raw) || k < 1 || k > 100) invalid('memories-k', '请输入 1–100 的整数');
      if (!thresholdRaw || !Number.isFinite(threshold) || threshold < 0 || threshold > 1) invalid('memories-threshold', '请输入 0–1 的数字');
      options = {k, threshold};
    } else {
      function date(id) {
        const raw = $(id).value;
        if (!raw && !$(id).validity.badInput) return '';
        const parsed = new Date(raw), parts = raw.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
        if (!parts || !Number.isFinite(parsed.getTime()) || [parsed.getFullYear(), parsed.getMonth() + 1,
          parsed.getDate(), parsed.getHours(), parsed.getMinutes(), parsed.getSeconds()]
          .some((value, i) => value !== Number(parts[i + 1] || 0))) {
          invalid(id, '请输入有效本地日期时间'); return '';
        }
        return parsed.toISOString();
      }
      options = {updatedAfter: date('memories-updated-after'), updatedBefore: date('memories-updated-before')};
      if (options.updatedAfter && options.updatedBefore && options.updatedAfter >= options.updatedBefore) {
        invalid('memories-updated-before', '结束时间必须晚于开始时间');
      }
    }
    if (errors.length) {
      $('memories-advanced').open = true;
      // A retained invalid search draft must remain correctable from Browse.
      if (search) $('memories-k').disabled = $('memories-threshold').disabled = false;
      $(errors[0]).focus(); return null;
    }
    return options;
  }
  query.oncompositionstart = () => { composing = true; };
  query.oncompositionend = () => { composing = false; onInput(query.value); updateClear(); };
  query.oninput = () => {
    if (!composing) { onInput(query.value); if (!query.value.trim()) query.value = ''; }
    updateClear();
  };
  query.addEventListener('keydown', event => { if (event.key === 'Enter' && (composing || event.isComposing)) event.preventDefault(); });
  $('memories-search-form').onsubmit = event => {
    event.preventDefault(); if (composing) return;
    if (!query.value.trim()) { onClear(); return; }
    const options = validate(true); if (options) onSubmit(options);
  };
  $('memories-search-clear').onclick = () => { query.value = ''; onClear(); updateClear(); query.focus(); };
  $('memories-advanced-form').onsubmit = event => {
    event.preventDefault(); const options = validate(searching); if (options) onApply(options);
  };
  function updateClear() { $('memories-search-clear').disabled = !query.value && !searching; }
  function update(state) {
    searching = Boolean(state.submittedQuery);
    if (query.value !== state.inputQuery && !composing) query.value = state.inputQuery;
    $('memories-k').disabled = $('memories-threshold').disabled = !searching;
    $('memories-updated-after').disabled = $('memories-updated-before').disabled = searching;
    updateClear();
  }
  function clearDates() {
    $('memories-updated-after').value = $('memories-updated-before').value = '';
    ids.filter(id => id.includes('updated')).forEach(id => error(id, ''));
  }
  return {update, clearDates};
}
