// Search form and unapplied advanced field drafts; no API calls.
import { $ } from './core.js';
import { t } from './i18n.js';

export function createMemoriesSearch({onInput, onSubmit, onClear, onApply, onMode}) {
  let searching = false, composing = false;
  let mode = 'semantic';
  const modeSelect = $('memories-search-mode');
  const drafts = {semantic: {k: '10', score: '0'}, lexical: {k: '10', score: ''}, anchor: {k: '10', score: ''}};
  const query = $('memories-query');
  const advanced = $('memories-advanced'), advancedToggle = $('memories-advanced-toggle');
  function setAdvancedOpen(open) {
    if (!open && advanced.contains(document.activeElement)) advancedToggle.focus();
    advanced.hidden = !open;
    advancedToggle.setAttribute('aria-expanded', String(open));
  }
  advancedToggle.onclick = () => setAdvancedOpen(advanced.hidden);
  const ids = ['memories-k', 'memories-threshold', 'memories-updated-after', 'memories-updated-before'];
  function error(id, message) {
    const field = $(id), output = $(id + '-error');
    field.setAttribute('aria-invalid', String(Boolean(message)));
    field.setAttribute('aria-describedby', output.id + (id === 'memories-threshold' ? ' memories-threshold-help' : '')); output.textContent = message;
  }
  function validate(search) {
    ids.forEach(id => error(id, ''));
    const errors = [];
    const invalid = (id, message) => { error(id, message); errors.push(id); };
    let options = {};
    if (search) {
      const raw = $('memories-k').value.trim(), k = Number(raw), thresholdRaw = $('memories-threshold').value.trim();
      const threshold = Number(thresholdRaw);
      if (!/^\d+$/.test(raw) || k < 1 || k > 100) invalid('memories-k', t('memories.search.error.k'));
      const semantic = mode === 'semantic';
      const numeric = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(thresholdRaw);
      if ((semantic || thresholdRaw) && (!numeric || !Number.isFinite(threshold) || threshold < 0 || (semantic && threshold > 1))) {
        invalid('memories-threshold', semantic ? t('memories.search.error.thresholdSemantic') : t('memories.search.error.thresholdLexical'));
      }
      options = semantic ? {k, threshold} : {k, minScore: thresholdRaw ? threshold : null};
    }
    function date(id) {
      const raw = $(id).value;
      if (!raw && !$(id).validity.badInput) return '';
      const parsed = new Date(raw), parts = raw.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
      if (!parts || !Number.isFinite(parsed.getTime()) || [parsed.getFullYear(), parsed.getMonth() + 1,
        parsed.getDate(), parsed.getHours(), parsed.getMinutes(), parsed.getSeconds()]
        .some((value, i) => value !== Number(parts[i + 1] || 0))) {
        invalid(id, t('memories.search.error.date')); return '';
      }
      return parsed.toISOString();
    }
    options = {...options, updatedAfter: date('memories-updated-after'), updatedBefore: date('memories-updated-before')};
    if (options.updatedAfter && options.updatedBefore && options.updatedAfter >= options.updatedBefore) {
      invalid('memories-updated-before', t('memories.search.error.dateOrder'));
    }
    if (errors.length) {
      setAdvancedOpen(true);
      // A retained invalid search draft must remain correctable from Browse.
      if (search) $('memories-k').disabled = $('memories-threshold').disabled = false;
      $(errors[0]).focus(); return null;
    }
    return options;
  }
  modeSelect.onchange = () => onMode(modeSelect.value);
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
    if (mode !== state.searchMode) {
      drafts[mode] = {k: $('memories-k').value, score: $('memories-threshold').value};
      mode = state.searchMode;
      $('memories-k').value = drafts[mode].k; $('memories-threshold').value = drafts[mode].score;
      ['memories-k', 'memories-threshold'].forEach(id => error(id, ''));
    }
    modeSelect.value = mode;
    $('memories-threshold-label').textContent = mode === 'semantic' ? t('memories.advanced.threshold') : t('memories.search.threshold.lexical');
    $('memories-threshold-help').textContent = mode === 'semantic' ? t('memories.advanced.thresholdHelp') : t('memories.search.thresholdHelp.lexical');
    searching = Boolean(state.submittedQuery);
    if (query.value !== state.inputQuery && !composing) query.value = state.inputQuery;
    $('memories-k').disabled = $('memories-threshold').disabled = !searching;
    updateClear();
  }
  function clearDates() {
    $('memories-updated-after').value = $('memories-updated-before').value = '';
    ids.filter(id => id.includes('updated')).forEach(id => error(id, ''));
  }
  function resetAnchorDraft() {
    drafts.anchor = {k: '10', score: ''};
    if (mode === 'anchor') {
      $('memories-k').value = '10'; $('memories-threshold').value = '';
      ['memories-k', 'memories-threshold'].forEach(id => error(id, ''));
    }
  }
  return {update, clearDates, resetAnchorDraft};
}
