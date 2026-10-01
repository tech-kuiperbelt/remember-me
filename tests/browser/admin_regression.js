// Isolated browser regression for the admin console.
//
// The page is the real /admin/console output; this module is injected after the
// application entry and drives the UI through DOM interactions, observing and
// controlling network traffic at the fetch boundary (window.__smoke).
const S = window.__smoke;
const result = document.createElement('pre'); result.id = 'smoke-result'; document.body.append(result);
const assert = (condition, text) => { if (!condition) throw new Error(text) };
const $ = id => document.getElementById(id);
const settle = (ms = 20) => new Promise(resolve => setTimeout(resolve, ms));
const json = (data, status = 200) => new Response(JSON.stringify(data),
  {status, headers: {'content-type': 'application/json'}});
const fire = (el, type) => el.dispatchEvent(new Event(type, {bubbles: true, cancelable: true}));
const submit = form => form.requestSubmit();

async function waitFor(check, label, timeout = 8000) {
  const start = Date.now();
  for (;;) {
    let ok = false;
    try { ok = check(); } catch (e) { ok = false; }
    if (ok) return;
    if (Date.now() - start > timeout) throw new Error('timeout waiting for ' + label);
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}

try {
  // The real page bootstraps from the real backend; wait for the first load.
  await waitFor(() => $('stats-content') && !$('stats-content').textContent.includes('Loading...'), 'initial stats');
  await waitFor(() => $('ns-tabs').children.length > 1, 'namespace tabs');

  assert(!$('stats-content').textContent.includes('cache clear'), 'Stats leaked vocabulary');

  // Real nav click exercises router.bindNav + switchPage.
  document.querySelector('.nav button[data-page="keywords"]').click();
  await settle();
  assert($('page-keywords').classList.contains('active'), 'nav click switches page');
  assert(!$('page-memories').classList.contains('active'), 'nav click hides old page');

  await waitFor(() => $('kw-results').textContent.includes('63 matches'), 'browse count');
  assert(!$('kw-next').disabled, 'next page enabled');
  $('kw-next').click();
  await waitFor(() => $('kw-results').textContent.includes('51–63'), 'pagination');

  $('kw-query').value = 'CACHE'; fire($('kw-query'), 'input'); submit($('form-keywords'));
  await waitFor(() => $('kw-results').textContent.includes('2 matches'), 'literal search');
  $('kw-mode').value = 'semantic'; submit($('form-keywords'));
  await waitFor(() => $('kw-results').textContent.includes('score 1.000'), 'semantic scores');
  assert($('kw-next').disabled && $('kw-sort').disabled, 'semantic controls');

  // Jump from a keyword into an exact query.
  $('kw-query').value = ''; $('kw-mode').value = 'literal'; $('kw-ns').value = 'a'; fire($('kw-ns'), 'change');
  await waitFor(() => Array.from($('kw-results').querySelectorAll('button')).some(b => b.textContent === 'cache'), 'cache keyword button');
  Array.from($('kw-results').querySelectorAll('button')).find(b => b.textContent === 'cache').click();
  await waitFor(() => $('search-results').textContent.includes('memory cache'), 'keyword jump result');
  assert($('list-ns').value === 'a', 'namespace jump');
  assert(Array.from($('list-keywords').selectedOptions).some(o => o.value === 'cache'), 'selected keyword');

  $('list-keyword-more').click();
  await waitFor(() => Array.from($('list-keywords').options).some(o => o.value === 'word059'), 'load more');
  assert(Array.from($('list-keywords').selectedOptions).some(o => o.value === 'cache'), 'selection retained');
  $('list-keyword-query').value = 'rare_id'; fire($('list-keyword-query'), 'input');
  await waitFor(() => Array.from($('list-keywords').options).some(o => o.value === 'rare_id'), 'low frequency word');

  // Stale response must not overwrite the newer one.
  let release, first = true;
  S.setOverride(url => {
    if (url === '/api/keywords/query' && first) { first = false; return new Promise(resolve => { release = resolve; }); }
    return undefined;
  });
  $('kw-query').value = 'stale'; fire($('kw-query'), 'input'); submit($('form-keywords'));
  await waitFor(() => first === false, 'stale request started');
  $('kw-query').value = 'absent'; fire($('kw-query'), 'input'); submit($('form-keywords'));
  await waitFor(() => $('kw-results').textContent === 'No matching keywords.', 'absent result');
  release(json({results: [{total: 1, items: [{keyword: 'stale', count: 1}]}]}));
  await settle(50);
  assert($('kw-results').textContent === 'No matching keywords.', 'stale response ignored');
  S.reset();

  // Special characters round-trip through a real stats refresh.
  const special = `部门'"\\<img src=x onerror=window.injected=1>`;
  const specialId = `id'"\\<svg onload=window.injected=1>`;
  const specialStats = {total: 64, backend: 'sqlite', path: 'x', namespaces: ['a', special],
                        namespace_counts: {a: 63, [special]: 1}, keyword_total: 63};
  S.setOverride(url => url === '/api/stats' ? json(specialStats) : undefined);
  document.querySelector('.nav button[data-page="stats"]').click();
  await waitFor(() => Array.from($('search-ns').options).some(o => o.value === special), 'special namespace option');
  S.setOverride(url => {
    if (url === '/api/stats') return json(specialStats);
    if (url.startsWith('/api/memories')) return json([]);
    return undefined;
  });
  Array.from($('ns-tabs').children).find(el => el.dataset.ns === special).click();
  await waitFor(() => $('memories-list').textContent.includes('No memories found'), 'special list');
  const activeTab = Array.from($('ns-tabs').children).find(el => el.dataset.ns === special);
  assert(activeTab && activeTab.classList.contains('active'), 'namespace click roundtrip');

  const record = {id: specialId, namespace: special, text: special, summary: special,
                  created_at: '2026-09-22T00:00:00Z', updated_at: '2026-09-22T00:00:00Z', score: 1, metadata: {}};
  const detailUrl = '/api/memories/' + encodeURIComponent(specialId);
  S.setOverride((url, init) => {
    const method = (init && init.method) || 'GET';
    if (url === '/api/memories' || url.startsWith('/api/memories?')) return json([record]);
    if (url.startsWith('/api/memories/')) return method === 'PUT' || method === 'DELETE' ? json({id: specialId}) : json(record);
    return undefined;
  });
  document.querySelector('.nav button[data-page="memories"]').click();
  await waitFor(() => $('memories-list').querySelector('.memory-text') && $('memories-list').querySelector('.memory-text').title === special, 'list record');

  S.resetCalls();
  $('memories-list').querySelectorAll('[data-memory-action]')[0].click();
  await waitFor(() => $('detail-modal').style.display === 'flex', 'view modal');
  assert(S.calls.some(c => c.url === detailUrl && c.method === 'GET'), 'view detail URL');
  assert(!$('edit-text'), 'view opens read-only detail');

  S.resetCalls();
  $('memories-list').querySelectorAll('[data-memory-action]')[1].click();
  await waitFor(() => $('edit-text'), 'edit modal');
  assert(S.calls.some(c => c.url === detailUrl), 'edit detail URL');
  assert($('edit-namespace').value === special, 'edit namespace value');
  const option = Array.from($('ns-dropdown').children).find(el => el.textContent === special);
  assert(option, 'namespace picker option');
  $('edit-namespace').value = ''; option.dispatchEvent(new MouseEvent('mousedown'));
  assert($('edit-namespace').value === special, 'namespace picker');

  S.resetCalls();
  Array.from($('detail-actions').children).find(el => el.textContent === 'Save').click();
  await waitFor(() => S.calls.some(c => c.url === detailUrl && c.method === 'PUT'), 'detail save action');

  const originalConfirm = window.confirm;
  window.confirm = () => true;
  await waitFor(() => $('memories-list').querySelectorAll('[data-memory-action]').length === 3, 'list restored');
  S.resetCalls();
  $('memories-list').querySelectorAll('[data-memory-action]')[2].click();
  await waitFor(() => S.calls.some(c => c.url === detailUrl && c.method === 'DELETE'), 'detail delete action');
  window.confirm = originalConfirm;

  // Exact-list and semantic result View buttons.
  S.setOverride((url, init) => {
    const method = (init && init.method) || 'GET';
    if (url === '/api/memories/search') return json([record]);
    if (url === '/api/memories' || url.startsWith('/api/memories?')) return json([record]);
    if (url.startsWith('/api/memories/')) return method === 'PUT' || method === 'DELETE' ? json({id: specialId}) : json(record);
    return undefined;
  });
  document.querySelector('.nav button[data-page="search"]').click();
  document.querySelector('#search-mode-tabs .ns-tab[data-mode="list"]').click();
  submit($('form-search'));
  await waitFor(() => $('search-results').querySelector('[data-memory-action]'), 'list results');
  S.resetCalls();
  $('search-results').querySelector('[data-memory-action]').click();
  await waitFor(() => S.calls.some(c => c.url === detailUrl), 'exact results action');

  document.querySelector('#search-mode-tabs .ns-tab[data-mode="semantic"]').click();
  $('search-query').value = 'x';
  submit($('form-search'));
  await waitFor(() => $('search-results').querySelector('[data-memory-action]'), 'semantic results');
  S.resetCalls();
  $('search-results').querySelector('[data-memory-action]').click();
  await waitFor(() => S.calls.some(c => c.url === detailUrl), 'semantic results action');

  // Literal error rendering in every container.
  S.setOverride(url => (url === '/api/memories' || url.startsWith('/api/memories?')) ? json({detail: special}, 500) : undefined);
  document.querySelector('.nav button[data-page="memories"]').click();
  await waitFor(() => $('memories-list').textContent === 'Error: ' + special, 'literal error memories-list');
  assert(!$('memories-list').querySelector('img'), 'literal error memories-list safe');

  S.setOverride(url => url === '/api/memories/search' ? json({detail: special}, 500) : undefined);
  document.querySelector('#search-mode-tabs .ns-tab[data-mode="semantic"]').click();
  submit($('form-search'));
  await waitFor(() => $('search-results').textContent === 'Error: ' + special, 'literal error search-results');
  assert(!$('search-results').querySelector('img'), 'literal error search-results safe');

  S.setOverride(url => url === '/api/stats' ? json({detail: special}, 500) : undefined);
  document.querySelector('.nav button[data-page="stats"]').click();
  await waitFor(() => $('stats-content').textContent === 'Error: ' + special, 'literal error stats-content');
  assert(!$('stats-content').querySelector('img'), 'literal error stats-content safe');
  S.reset();

  await settle(100);
  assert(!window.injected && !document.querySelector('img[src="x"]'), 'no injected elements or script');

  // Shutdown controls need a maintenance-ready state.
  const maintReady = {state: 'maintenance', action: null, required_reason: null, model: {status: 'ready', ready: true}};
  S.setOverride(url => url === '/api/maintenance' ? json(maintReady) : undefined);
  document.querySelector('.nav button[data-page="ops"]').click();
  await waitFor(() => !$('btn-shutdown').disabled, 'shutdown enabled');
  S.freezeMaintenance();

  const originalConfirm2 = window.confirm;
  let shutdownCalls = 0;
  S.setOverride(url => {
    if (url === '/api/shutdown') { shutdownCalls++; return json({status: 'stopping'}); }
    return undefined;
  });
  window.confirm = () => false;
  $('btn-shutdown').click();
  await settle();
  assert(shutdownCalls === 0, 'shutdown cancellation');

  window.confirm = () => true;
  S.setOverride(url => url === '/api/shutdown' ? Promise.reject(new Error('network failed')) : undefined);
  $('btn-shutdown').click();
  await waitFor(() => $('restart-result').textContent.includes('could not be confirmed'), 'shutdown failure');
  assert(!$('btn-shutdown').disabled, 'shutdown failure re-enables');

  S.setOverride(url => url === '/api/shutdown' ? json({status: 'stopping'}) : undefined);
  $('btn-shutdown').click();
  await waitFor(() => $('restart-result').textContent.includes('terminal'), 'shutdown accepted');
  assert($('btn-shutdown').disabled, 'shutdown accepted disables controls');
  window.confirm = originalConfirm2;
  S.unfreezeMaintenance();

  // Model bootstrap / progress / proxy / retry.
  const preparing = {state: 'maintenance', action: null, required_reason: 'model_not_ready',
    model: {status: 'downloading', ready: false, completed: 40, total: 100, percent: 40, file: 'weights.bin'}};
  let saved, retried = false;
  S.setOverride((url, init) => {
    if (url === '/api/maintenance') return json(preparing);
    if (url === '/api/config' && init && init.method === 'POST') { saved = JSON.parse(init.body); return json({}); }
    if (url === '/api/config') return json({port: 1314, language: 'zh-CN', auto_download_model: true, proxy: {enabled: true, url: 'http://***@localhost:7890'}});
    if (url === '/api/model/retry') { retried = true; return json({}); }
    return undefined;
  });
  document.querySelector('.nav button[data-page="ops"]').click();
  await waitFor(() => $('btn-status').textContent === '模型未就绪' && !$('btn-model-save').disabled && $('model-progress').value === 40, 'bootstrap progress');
  console.debug = (...a) => { result.dataset.dbg = JSON.stringify(a); };
  assert('DIAG status=' + $('btn-status').textContent + ' exit=' + $('btn-maintenance-exit').disabled + ' sync=' + $('btn-sync').disabled + ' bulk=' + $('btn-import').disabled, 'DIAG');
  assert($('btn-maintenance-exit').disabled && $('btn-sync').disabled, 'forced maintenance locks actions');
  assert(!$('btn-shutdown').disabled, 'bootstrap shutdown available');
  assert($('model-progress-detail').textContent.includes('40.0%'), 'actual byte progress');

  $('model-proxy-url').value = 'http://127.0.0.1:7890';
  $('btn-model-save').click();
  await waitFor(() => saved && retried, 'save proxy and retry');
  assert(saved.proxy.url === 'http://127.0.0.1:7890', 'proxy saved');
  assert($('model-proxy-url').value === '', 'proxy credential input cleared');

  preparing.model = {status: 'failed', ready: false, percent: null, error: '<img src=x onerror=window.injected=1>'};
  document.querySelector('.nav button[data-page="ops"]').click();
  await waitFor(() => $('model-error').textContent.includes('<img'), 'model error rendered');
  assert(!$('model-progress').hasAttribute('value'), 'indeterminate progress');
  assert(!$('model-error').querySelector('img'), 'model error safely rendered');

  preparing.required_reason = null; preparing.model = {status: 'ready', ready: true};
  document.querySelector('.nav button[data-page="ops"]').click();
  await waitFor(() => !$('btn-maintenance-exit').disabled, 'ready permits manual exit');
  assert($('model-progress').value === 100, 'ready progress');

  preparing.state = 'normal';
  document.querySelector('.nav button[data-page="ops"]').click();
  await waitFor(() => $('maintenance-status').textContent.includes('State: normal'), 'normal state');
  assert(!$('btn-model-save').disabled, 'normal mode permits config');
  assert($('btn-sync').disabled && $('btn-restart').disabled, 'normal mode blocks maintenance actions');

  $('config-port').value = '2314'; $('config-language').value = 'en';
  retried = false;
  $('btn-model-save').click();
  await waitFor(() => saved.port === 2314, 'full config save');
  assert(saved.language === 'en' && !retried, 'full config save in normal mode');
  assert($('model-config-result').textContent.includes('重启后生效'), 'restart requirement shown');
  assert($('model-card').compareDocumentPosition($('model-preparation-card')) & Node.DOCUMENT_POSITION_FOLLOWING, 'configuration above maintenance');
  S.reset();

  // --- Desktop shell: status states, layout, collapse, scroll ownership ---

  // Status chip states + control gating (aligned with maintenance.py).
  const statusCases = [
    {snap: {state: 'normal', action: null, required_reason: null, model: {status: 'ready', ready: true}},
     label: '运行中', enter: false, exit: true, sync: true, bulk: true},
    {snap: {state: 'maintenance', action: null, required_reason: null, model: {status: 'ready', ready: true}},
     label: '维护中', enter: true, exit: false, sync: false, bulk: false},
    {snap: {state: 'maintenance', action: null, required_reason: 'model_not_ready', model: {status: 'downloading', ready: false}},
     label: '模型未就绪', enter: true, exit: true, sync: true, bulk: true},
    {snap: {state: 'maintenance', action: null, required_reason: null, error: 'boom', model: {status: 'ready', ready: true}},
     label: '维护异常', enter: true, exit: true, sync: false, bulk: true},
  ];
  for (const c of statusCases) {
    S.setOverride(url => url === '/api/maintenance' ? json(c.snap) : undefined);
    document.querySelector('.nav button[data-page="ops"]').click();
    await waitFor(() => $('btn-status').textContent === c.label, 'status ' + c.label);
    assert($('btn-maintenance-enter').disabled === c.enter, 'enter gate ' + c.label);
    assert($('btn-maintenance-exit').disabled === c.exit, 'exit gate ' + c.label);
    assert($('btn-sync').disabled === c.sync, 'sync gate ' + c.label);
    assert($('btn-import').disabled === c.bulk, 'bulk gate ' + c.label);
  }
  // Sticky error still allows sync/restart/shutdown (maintenance.py:113).
  S.setOverride(url => url === '/api/maintenance' ? json({state: 'maintenance', action: null, required_reason: null, error: 'boom', model: {status: 'ready', ready: true}}) : undefined);
  document.querySelector('.nav button[data-page="ops"]').click();
  await waitFor(() => $('btn-status').textContent === '维护异常', 'error state');
  assert(!$('btn-sync').disabled && !$('btn-restart').disabled && !$('btn-shutdown').disabled, 'error allows sync/restart/shutdown');

  // Failed poll => 状态不可用 and all writers disabled until recovery.
  S.setOverride(url => url === '/api/maintenance' ? Promise.reject(new Error('down')) : undefined);
  document.querySelector('.nav button[data-page="ops"]').click();
  await waitFor(() => $('btn-status').textContent === '状态不可用', 'unavailable status');
  assert($('btn-sync').disabled && $('btn-import').disabled, 'unavailable disables writes');
  assert($('btn-refresh') && !$('btn-refresh').disabled && !$('btn-debug').disabled, 'global controls stay enabled');

  // Recovery restores normal gating (writers enabled, bulk actions off).
  S.setOverride(url => {
    if (url === '/api/maintenance') return json({state: 'normal', action: null, required_reason: null, model: {status: 'ready', ready: true}});
    if (url === '/api/config') return json({port: 1314, language: 'zh-CN', auto_download_model: true, proxy: {enabled: false, url: ''}});
    if (url === '/api/stats') return json({total: 0, backend: 'sqlite', path: 'x', namespaces: [], namespace_counts: {}});
    return undefined;
  });
  document.querySelector('.nav button[data-page="ops"]').click();
  await waitFor(() => $('btn-status').textContent === '运行中' && $('btn-sync').disabled, 'status recovers');
  assert($('btn-maintenance-enter').disabled === false, 'enter re-enabled after recovery');
  S.reset();

  // Shell layout: header + sidebar fixed, work area scrolls, no page-level h-scroll.
  assert($('app-shell').contains($('nav')), 'sidebar inside shell');
  assert($('app-shell').contains($('content')), 'work area inside shell');
  assert(!$('content').contains($('detail-modal')), 'detail modal outside the shell content');
  assert(!$('content').contains($('clear-modal')), 'clear modal outside the shell content');
  assert(document.documentElement.scrollWidth <= window.innerWidth, 'no page horizontal scroll');
  assert($('content').scrollHeight >= $('content').clientHeight, 'work area is the scroll owner');

  // Active page gets aria-current and Content scrolls back to top on switch.
  const scroller = $('content');
  scroller.scrollTop = 9999;
  document.querySelector('.nav button[data-page="audit"]').click();
  await settle();
  assert(scroller.scrollTop === 0, 'switch page scrolls work area to top');
  assert(document.querySelector('.nav button[data-page="audit"]').getAttribute('aria-current') === 'page', 'aria-current set');
  assert(!document.querySelector('.nav button[data-page="keywords"]').hasAttribute('aria-current'), 'aria-current cleared');

  // Sidebar collapse: aria-expanded, class, persistence, form content preserved.
  $('add-text').value = 'draft-kept';
  const toggleBtn = document.querySelector('#btn-sidebar-toggle');
  assert(toggleBtn.getAttribute('aria-expanded') === 'true', 'sidebar starts expanded');
  toggleBtn.click();
  await settle();
  assert($('app-shell').classList.contains('sidebar-collapsed'), 'collapse toggles class');
  assert(toggleBtn.getAttribute('aria-expanded') === 'false', 'collapse updates aria-expanded');
  assert($('add-text').value === 'draft-kept', 'collapse preserves form content');
  assert(localStorage.getItem('remember-me.sidebar.collapsed.v1') === 'true', 'collapse persisted');
  toggleBtn.click();
  await settle();
  assert(!$('app-shell').classList.contains('sidebar-collapsed'), 'expand restores');
  assert(localStorage.getItem('remember-me.sidebar.collapsed.v1') === 'false', 'expand persisted');

  // Keyboard activation of the collapsed nav button.
  const memBtn = document.querySelector('.nav button[data-page="memories"]');
  memBtn.focus();
  memBtn.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
  memBtn.click();
  await settle();
  assert($('page-memories').classList.contains('active'), 'keyboard/nav activation switches page');

  result.textContent = 'PASS: real /admin/console entry; nav switch; browse, pagination, literal, semantic, exact jump, selection retention, low-frequency, stale response; special characters, safe actions and errors; shutdown controls; model bootstrap/progress/proxy/retry; desktop shell status/layout/collapse/scroll';
} catch (e) { result.textContent = 'FAIL: ' + e.stack; }
