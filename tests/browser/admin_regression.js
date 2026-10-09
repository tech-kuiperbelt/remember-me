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

async function runThemeScenario(scenario) {
  if (!scenario.startsWith('theme-')) return false;
    const memoriesOnly = scenario.endsWith('memories-dark') || scenario.endsWith('memories-light');
    const modalOnly = scenario.endsWith('modal-dark');
    const collapsedOnly = scenario.endsWith('collapsed-dark');
    const refreshOnly = scenario.endsWith('refresh-dark');
    const filtersOnly = scenario.endsWith('filters-light') || scenario.endsWith('filters-dark');
    const searchOnly = scenario.endsWith('search-light') || scenario.endsWith('search-dark');
    const advancedOnly = scenario.endsWith('advanced-light') || scenario.endsWith('advanced-dark');
    const filtersError = scenario.endsWith('filters-error-light');
    const blocked = scenario.endsWith('blocked');
    const system = scenario.endsWith('system');
    const unseeded = scenario.endsWith('default') || scenario.endsWith('invalid');
    const scheme = scenario.endsWith('dark') ? 'dark' : 'light';
    // The test seeds the stored preference before first paint; the boot script
    // must apply it without waiting for the app entry.
    assert(document.documentElement.dataset.theme === ((blocked || unseeded) ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : scheme), 'first-paint theme');
    assert(document.documentElement.dataset.themePreference === (blocked || system || unseeded ? 'system' : scheme), 'seeded preference applied');

    if (refreshOnly) {
      if (sessionStorage.getItem('theme-refresh-stage') !== 'verify') {
        sessionStorage.setItem('theme-refresh-stage', 'verify');
        history.replaceState({}, '', location.pathname + '?smoke=' + scenario);
        location.reload();
        return true;
      }
      sessionStorage.removeItem('theme-refresh-stage');
      assert(document.documentElement.dataset.theme === 'dark', 'stored theme survives reload');
      result.textContent = 'PASS: dark theme survives reload without seed';
      return true;
    }

    if (filtersError) {
      await waitFor(() => $('stats-content').textContent.includes('Initial Stats failure'), 'initial stats error');
      $('memories-categories').click();
      assert($('memories-categories-popover').textContent.includes('Initial Stats failure'), 'category initial error visible');
      assert(!document.querySelector('.memory-category-option'), 'initial failure is not an empty vocabulary');
      S.reset();
      [...$('memories-categories-popover').querySelectorAll('button')].find(node => node.textContent === 'Retry').click();
      await waitFor(() => document.querySelectorAll('.memory-category-option').length > 0, 'category retry recovers');
      result.textContent = 'PASS: category first-load failure and recovery'; return true;
    }
    if (memoriesOnly || modalOnly || collapsedOnly || filtersOnly || searchOnly || advancedOnly) {
      await waitFor(() => $('memories-list')?.getAttribute('aria-busy') === 'false' && $('memories-list').querySelector('.memory-open'), 'memories');
      assert($('page-memories').classList.contains('active'), 'memories page visible');
      if (searchOnly) {
        $('memories-query').value = 'cache'; fire($('memories-query'), 'input'); submit($('memories-search-form'));
        await waitFor(() => $('memories-list').querySelector('.memory-search-score'), 'search score');
      }
      if (advancedOnly) $('memories-advanced').open = true;
      assert(document.documentElement.scrollWidth <= innerWidth, 'workspace fits viewport');
      if (filtersOnly) {
        $('memories-categories').click();
        await waitFor(() => document.querySelector('.memory-category-option'), 'filter candidates');
        const checkbox = [...document.querySelectorAll('.memory-category-option input')].find(node => node.dataset.value === 'design');
        checkbox.click();
        document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles:true}));
        $('memories-keywords').click();
        $('memories-keywords-query').value = 'word'; fire($('memories-keywords-query'), 'input');
        await waitFor(() => document.querySelector('[data-keyword]'), 'filter suggestions');
        document.querySelector('[data-keyword]').click();
        await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'filter result');
        const box = document.querySelector('.memory-filter-popover').getBoundingClientRect();
        assert(box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight, 'filter popover fits viewport');
        assert(document.documentElement.scrollWidth <= innerWidth, 'filters do not overflow page');
      }
      if (modalOnly) {
        $('memories-list').querySelector('.memory-open').click();
        await waitFor(() => $('detail-edit'), 'dark detail modal');
        const themeSelect = $('theme-select');
        themeSelect.value = 'light'; themeSelect.dispatchEvent(new Event('change', {bubbles:true}));
        assert($('detail-modal').style.display === 'flex', 'theme switch keeps modal open');
        themeSelect.value = 'dark'; themeSelect.dispatchEvent(new Event('change', {bubbles:true}));
      }
      if (collapsedOnly) {
        $('nav').click();
        assert($('app-shell').classList.contains('sidebar-collapsed'), 'dark sidebar collapsed');
      }
      result.textContent = 'PASS: ' + scheme + ' ' + (modalOnly ? 'detail modal' : collapsedOnly ? 'collapsed sidebar' : 'Memories') + ' screenshot';
      return true;
    }

    document.querySelector('.nav button[data-page="ops"]').click(); await settle();
    const select = $('theme-select');
    assert(select && select.value === (blocked || system || unseeded ? 'system' : scheme), 'appearance select reflects preference');

    if (system) {
      window.__themeMedia.set(true);
      assert(document.documentElement.dataset.theme === 'dark', 'system change applies dark');
      select.value = 'light'; select.dispatchEvent(new Event('change', {bubbles:true}));
      window.__themeMedia.set(false);
      window.__themeMedia.set(true);
      assert(document.documentElement.dataset.theme === 'light', 'manual choice ignores system changes');
      select.value = 'system'; select.dispatchEvent(new Event('change', {bubbles:true}));
      assert(document.documentElement.dataset.theme === 'dark', 'system choice resumes following OS');
      result.textContent = 'PASS: system theme, live changes, manual override';
      return true;
    }
    if (blocked || unseeded) {
      select.value = 'dark'; select.dispatchEvent(new Event('change', {bubbles:true}));
      assert(document.documentElement.dataset.theme === 'dark', 'theme switch applies dark');
      select.value = 'light'; select.dispatchEvent(new Event('change', {bubbles:true}));
      assert(document.documentElement.dataset.theme === 'light', 'theme switch applies light');
      result.textContent = 'PASS: ' + (blocked ? 'storage unavailable' : 'default or invalid preference') + '; theme stays interactive';
      return true;
    }

    // Manual override applies immediately without saving or reloading.
    $('memories-query').value = 'theme-kept';
    const workArea = document.querySelector('.content');
    workArea.scrollTop = 120;
    const priorScroll = workArea.scrollTop;
    select.value = 'dark'; select.dispatchEvent(new Event('change', {bubbles: true}));
    await settle();
    assert(document.documentElement.dataset.theme === 'dark', 'manual dark applies');
    assert(document.documentElement.dataset.themePreference === 'dark', 'preference recorded');
    assert(localStorage.getItem('remember-me.theme.v1') === 'dark', 'preference persisted');
    select.value = 'light'; select.dispatchEvent(new Event('change', {bubbles: true}));
    await settle();
    assert(document.documentElement.dataset.theme === 'light', 'manual light applies');
    assert($('memories-query').value === 'theme-kept', 'theme switch preserves form content');
    assert(workArea.scrollTop === priorScroll, 'theme switch preserves scroll position');
    const bodyBg = getComputedStyle(document.body).backgroundColor;
    const panel = getComputedStyle(document.querySelector('.card')).backgroundColor;
    assert(bodyBg && panel && bodyBg !== panel, 'canvas and surface tokens resolve distinctly');

    // Appearance control stays usable while the service is unavailable.
    S.setOverride(url => url === '/api/maintenance' ? Promise.reject(new Error('down')) : undefined);
    document.querySelector('.nav button[data-page="ops"]').click();
    await waitFor(() => $('btn-status').textContent === '状态不可用', 'status unavailable');
    assert(!$('theme-select').disabled, 'appearance control usable when status unavailable');
    select.value = 'system'; select.dispatchEvent(new Event('change', {bubbles: true}));
    await settle();
    assert(document.documentElement.dataset.themePreference === 'system', 'system restored');
    S.reset();
    select.value = scheme; select.dispatchEvent(new Event('change', {bubbles: true}));
    assert(document.documentElement.dataset.theme === scheme, 'screenshot retains requested theme');

  result.textContent = 'PASS: theme ' + scheme + '; first-paint, manual override, persistence, form preserved, token colors, control under failure';
  return true;
}

try {
  const scenario = new URLSearchParams(location.search).get('smoke') || '';
  if (await runCreateScenario(scenario) || await runDetailScenario(scenario) || await runMemoriesVisualScenario(scenario) || await runThemeScenario(scenario)) { /* theme scenario finished */ }
  else {

  // The real page bootstraps from the real backend; wait for the first load.
  await waitFor(() => $('stats-content') && !$('stats-content').textContent.includes('Loading...'), 'initial stats');
  await waitFor(() => $('memories-namespace').options.length > 1, 'namespace options');

  assert(!$('stats-content').textContent.includes('cache clear'), 'Stats leaked vocabulary');

  // Browse pagination uses the complete filtered total; refresh fixes a removed last page.
  const inventory = Array.from({length: 41}, (_, i) => ({id: 'browse-' + i, namespace: 'a',
    summary: 'Browse ' + i, text: 'Browse ' + i + ' body preview',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-02-01T00:00:00Z',
    metadata: {categories: ['design', 'api', 'verification'], keywords: ['cache', 'transaction', 'locking', 'sqlite', 'search', 'overflow']}}));
  let live = [...inventory];
  S.setOverride((url, init) => {
    if (url.startsWith('/api/memories/page?')) {
      const params = new URL(url, location.origin).searchParams;
      const offset = Number(params.get('offset')), limit = Number(params.get('limit'));
      const rows = params.get('sort_by') === 'created_at' ? [...live].reverse() : live;
      return json({items: rows.slice(offset, offset + limit), total: live.length});
    }
    if (url.startsWith('/api/memories/browse-')) {
      const id = url.split('/').pop();
      if (init?.method === 'DELETE') { live = live.filter(row => row.id !== id); return json({}); }
      return json(live.find(row => row.id === id));
    }
    return undefined;
  });
  $('btn-refresh').click();
  await waitFor(() => $('memories-count').textContent === '41 memories', 'browse total');
  assert($('memories-list').querySelectorAll('.memory-row').length === 20, 'first page length');
  const firstOpen = $('memories-list').querySelector('.memory-open');
  assert(firstOpen.tagName === 'BUTTON', 'row opening has native keyboard activation');
  firstOpen.focus(); firstOpen.click();
  await waitFor(() => $('detail-modal').style.display === 'flex', 'keyboard entry detail');
  $('btn-detail-close').click();
  assert(document.activeElement === firstOpen, 'closing detail restores row focus');
  $('memory-row-1-detail-arrow').click();
  await waitFor(() => $('detail-modal').style.display === 'flex', 'right arrow opens detail');
  $('btn-detail-close').click();
  $('memories-list').querySelector('.memory-row-time').click();
  await waitFor(() => $('detail-modal').style.display === 'flex', 'time area opens detail');
  $('btn-detail-close').click();
  assert($('memory-row-1-namespace').textContent === '空间：a', 'namespace has explicit group label');
  $('memory-row-1').scrollIntoView({block:'center'});
  for (const id of ['memory-row-1-date', 'memory-row-1-namespace', 'memory-row-1-category-1', 'memory-row-1-keyword-1']) {
    const node = $(id), box = node.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    assert(hit && (hit === node || node.contains(hit)), 'hover reaches metadata rather than row button: ' + id);
    assert(Boolean(hit.closest('[title]')?.title), 'hover target has full value tooltip: ' + id);
  }
  assert($('memory-row-1-categories').querySelectorAll('.memory-category-chip').length === 3
    && $('memory-row-1-keywords').querySelectorAll('.memory-keyword-chip').length === 5, 'categories display three and keywords five independently');
  assert(!$('memory-row-1-category-more') && $('memory-row-1-keyword-more').textContent === '+1', 'overflow reflects independent limits');
  $('memory-row-1-keyword-more').click();
  assert(document.querySelector('.memory-tags-popover')?.textContent.includes('overflow')
    && !document.querySelector('.memory-tags-popover').textContent.includes('verification'), 'keyword overflow exposes all keywords only');
  assert($('detail-modal').style.display !== 'flex', 'tag overflow does not open detail');
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert(!document.querySelector('.memory-tags-popover'), 'Escape closes tags');
  assert(document.activeElement.classList.contains('memory-tags-more'), 'Escape restores tag focus');
  $('memories-sort').value = 'created_at'; fire($('memories-sort'), 'change');
  await waitFor(() => $('memories-list').querySelector('.memory-row-title')?.textContent === 'Browse 40', 'global sort');
  $('memories-sort').value = 'updated_at'; fire($('memories-sort'), 'change');
  await waitFor(() => $('memories-list').querySelector('.memory-row-title')?.textContent === 'Browse 0', 'browse sort reset');
  $('memories-pagination').querySelector('[aria-label="下一页"]').click();
  await waitFor(() => $('memories-pagination').textContent.includes('21–40 of 41'), 'second page');
  $('memories-pagination').querySelector('[aria-label="下一页"]').click();
  await waitFor(() => $('memories-pagination').textContent.includes('41–41 of 41'), 'last page');
  const approveDelete = window.confirm; window.confirm = () => true;
  const lastOpen = $('memories-list').querySelector('.memory-open');
  lastOpen.focus(); lastOpen.click();
  await waitFor(() => $('detail-modal').style.display === 'flex', 'last item detail');
  await waitFor(() => $('detail-more'), 'detail loaded'); $('detail-more').click(); $('detail-delete').click(); $('detail-confirm-delete').click();
  await waitFor(() => $('memories-pagination').textContent.includes('21–40 of 40'), 'delete adjusts page');
  window.confirm = approveDelete;
  $('memories-page-size').value = '10'; fire($('memories-page-size'), 'change');
  await waitFor(() => $('memories-pagination').textContent.includes('1–10 of 40'), 'page size resets page');
  $('memories-page-size').value = '20'; fire($('memories-page-size'), 'change');
  await waitFor(() => $('memories-pagination').textContent.includes('1–20 of 40'), 'restore page size');

  let releasePage;
  S.setOverride(url => {
    if (!url.startsWith('/api/memories/page?')) return undefined;
    if (url.includes('sort_by=created_at')) return new Promise(resolve => {releasePage = resolve;});
    return json({items: [{...inventory[0], summary: 'Latest result'}], total: 1});
  });
  $('memories-sort').value = 'created_at'; fire($('memories-sort'), 'change');
  await waitFor(() => releasePage, 'deferred browse response');
  $('memories-sort').value = 'updated_at'; fire($('memories-sort'), 'change');
  await waitFor(() => $('memories-list').textContent.includes('Latest result'), 'latest browse response');
  releasePage(json({items: inventory.slice(0, 20), total: 41})); await settle(50);
  assert($('memories-count').textContent === '1 memories' && $('memories-list').textContent.includes('Latest result'), 'stale browse ignored');
  let failPage = true;
  S.setOverride(url => url.startsWith('/api/memories/page?')
    ? failPage ? json({detail: 'Temporary failure'}, 500) : json({items: inventory.slice(0, 20), total: 41}) : undefined);
  $('btn-refresh').click();
  await waitFor(() => $('memories-list').textContent.includes('Temporary failure'), 'browse error');
  failPage = false;
  Array.from($('memories-list').querySelectorAll('button')).find(el => el.textContent === 'Retry').click();
  await waitFor(() => $('memories-count').textContent === '41 memories', 'browse retry');
  assert($('memories-sort').value === 'updated_at', 'retry retains sort');
  S.reset();

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
  S.resetCalls();
  Array.from($('kw-results').querySelectorAll('button')).find(b => b.textContent === 'cache').click();
  await waitFor(() => $('memories-list').textContent.includes('memory cache'), 'keyword jump result');
  assert(S.calls.filter(c => c.url.startsWith('/api/memories/page?')).length === 1, 'keyword jump queries exactly once');
  assert($('memories-namespace').value === 'a', 'namespace jump');
  assert($('memories-filters').textContent.includes('cache ×'), 'selected keyword');
  document.querySelector('.nav button[data-page="keywords"]').click();
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
  await waitFor(() => Array.from($('memories-namespace').options).some(o => o.value === special), 'special namespace option');
  S.setOverride(url => {
    if (url === '/api/stats') return json(specialStats);
    if (url.startsWith('/api/memories/page?')) return json({items: [], total: 0});
    return undefined;
  });
  $('memories-namespace').value = special; fire($('memories-namespace'), 'change');
  await waitFor(() => $('memories-list').textContent.includes('当前搜索或筛选没有匹配的记忆'), 'special list');
  assert($('memories-namespace').value === special, 'namespace selection roundtrip');

  const record = {id: specialId, namespace: special, text: special, summary: special,
                  created_at: '2026-09-22T00:00:00Z', updated_at: '2026-09-22T00:00:00Z', score: 1, metadata: {}};
  const detailUrl = '/api/memories/' + encodeURIComponent(specialId);
  S.setOverride((url, init) => {
    const method = (init && init.method) || 'GET';
    if (url.startsWith('/api/memories/page?')) return json({items: [record], total: 1});
    if (url === '/api/memories' || url.startsWith('/api/memories?')) return json([record]);
    if (url.startsWith('/api/memories/')) return method === 'PUT' || method === 'DELETE' ? json({id: specialId}) : json(record);
    return undefined;
  });
  document.querySelector('.nav button[data-page="memories"]').click();
  await waitFor(() => $('memories-list').querySelector('.memory-row-title') && $('memories-list').querySelector('.memory-row-title').title === special, 'list record');

  S.resetCalls();
  $('memories-list').querySelector('.memory-open').click();
  await waitFor(() => $('detail-edit'), 'view modal');
  assert(S.calls.some(c => c.url === detailUrl && c.method === 'GET'), 'view detail URL');
  assert(!$('edit-text'), 'view opens read-only detail');

  S.resetCalls();
  Array.from($('detail-actions').children).find(el => el.textContent === 'Edit').click();
  await waitFor(() => $('edit-text'), 'edit modal');
  assert(!S.calls.some(c => c.url === detailUrl), 'editing reuses authoritative detail');
  assert($('edit-namespace').value === special, 'edit namespace value');
  assert([...$('detail-namespaces').children].some(el=>el.value===special), 'namespace candidates');
  $('edit-text').value += '\nEdited'; fire($('edit-text'),'input');
  S.resetCalls(); $('detail-save').click();
  await waitFor(() => S.calls.some(c => c.url === detailUrl && c.method === 'PUT'), 'detail save action');
  await waitFor(() => $('detail-edit'), 'saved detail stays open'); $('btn-detail-close').click();

  const originalConfirm = window.confirm;
  window.confirm = () => true;
  await waitFor(() => $('memories-list').querySelector('.memory-open'), 'list restored');
  $('memories-list').querySelector('.memory-open').click();
  await waitFor(() => $('detail-modal').style.display === 'flex' && !$('edit-text'), 'delete detail');
  S.resetCalls();
  await waitFor(() => $('detail-more'), 'detail loaded'); $('detail-more').click(); $('detail-delete').click(); $('detail-confirm-delete').click();
  await waitFor(() => S.calls.some(c => c.url === detailUrl && c.method === 'DELETE'), 'detail delete action');
  window.confirm = originalConfirm;

  // Semantic rows share the same safe detail action as browse.
  S.setOverride((url, init) => {
    const method = init?.method || 'GET';
    if (url === '/api/memories/search') return json({items: [{...record, score: 0.91}], filter_resolution: null});
    if (url.startsWith('/api/memories/page?')) return json({items: [record], total: 1});
    if (url.startsWith('/api/memories/')) return method === 'PUT' || method === 'DELETE' ? json({id: specialId}) : json(record);
    return undefined;
  });
  $('memories-query').value = 'x'; fire($('memories-query'), 'input'); submit($('memories-search-form'));
  await waitFor(() => $('memories-list').querySelector('.memory-search-score'), 'semantic results');
  S.resetCalls(); $('memories-list').querySelector('.memory-open').click();
  await waitFor(() => S.calls.some(c => c.url === detailUrl), 'semantic results action');
  $('btn-detail-close').click();

  S.setOverride(url => url.startsWith('/api/memories/page?') ? json({detail: special}, 500) : undefined);
  $('memories-search-clear').click();
  await waitFor(() => $('memories-list').querySelector('p')?.textContent === 'Error: ' + special, 'literal error memories-list');
  assert(!$('memories-list').querySelector('img'), 'literal error memories-list safe');
  S.setOverride(url => url === '/api/memories/search' ? json({detail: special}, 500) : undefined);
  $('memories-query').value = 'x'; fire($('memories-query'), 'input'); submit($('memories-search-form'));
  await waitFor(() => $('memories-list').querySelector('p')?.textContent === 'Error: ' + special, 'literal search error');
  assert(!$('memories-list').querySelector('img'), 'literal search error safe');
  S.reset(); $('memories-search-clear').click();

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
    if (url === '/api/config') return json({port: 1314, language: 'zh-CN', auto_download_model: true, local_only: true, proxy: {enabled: true, url: 'http://***@localhost:7890'}});
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
  assert($('config-local-only').checked === true, 'local-only loaded from config');
  assert($('config-local-only-warning').hidden, 'LAN warning hidden when local-only');
  $('config-local-only').checked = false;
  $('config-local-only').dispatchEvent(new Event('change'));
  assert(!$('config-local-only-warning').hidden, 'LAN warning shown when local access disabled');
  retried = false;
  $('btn-model-save').click();
  await waitFor(() => saved.port === 2314, 'full config save');
  assert(saved.language === 'en' && saved.local_only === false && !retried, 'full config save in normal mode');
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
    if (url === '/api/config') return json({port: 1314, language: 'zh-CN', auto_download_model: true, local_only: true, proxy: {enabled: false, url: ''}});
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
  assert(document.title === 'Remember Me' && document.querySelector('.brand h1').textContent.trim() === 'Remember Me', 'shell brand name');
  assert(getComputedStyle(document.querySelector('.brand')).flexDirection === 'row', 'brand and server info share one line');
  assert($('server-info').textContent.trim() === 'MCP Server', 'server info remains available');
  assert(document.querySelector('#btn-refresh svg') && document.querySelector('#btn-debug svg'), 'global actions have inline icons');
  assert(getComputedStyle(document.querySelector('.app-header')).minHeight === '54px', 'compact shell header baseline');
  const sidebarRect = $('nav').getBoundingClientRect();
  assert(!$('nav').querySelector('.sidebar-toggle'), 'no separate sidebar toggle control');
  assert($('nav').tabIndex === 0, 'nav background is keyboard focusable for collapse toggle');
  assert(Math.abs(document.querySelector('#nav-memories').getBoundingClientRect().top - sidebarRect.top) <= 1, 'sidebar menu starts at top without reserved toggle space');
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

  // Blank-nav collapse: accessible state, persistence, menu routing and form content preserved.
  $('memories-query').value = 'draft-kept';
  $('btn-refresh').focus();
  const nav = $('nav');
  assert(nav.getAttribute('aria-label').includes('已展开'), 'sidebar starts expanded');
  nav.click();
  await settle();
  assert($('app-shell').classList.contains('sidebar-collapsed'), 'collapse toggles class');
  assert(nav.getAttribute('aria-label').includes('已折叠'), 'collapse updates accessible state');
  const memBtn = document.querySelector('.nav button[data-page="memories"]');
  const collapsedMemoriesRect = memBtn.getBoundingClientRect();
  assert(Math.abs(collapsedMemoriesRect.top - sidebarRect.top) <= 1, 'collapsed Memories item starts at top');
  assert(memBtn.getAttribute('aria-label') === 'Memories', 'collapsed nav keeps accessible name');
  memBtn.blur();
  memBtn.focus();
  assert(!$('nav-tooltip').hidden && $('nav-tooltip').textContent === 'Memories', 'collapsed nav tooltip appears on keyboard focus');
  assert(memBtn.getAttribute('aria-describedby') === 'nav-tooltip', 'collapsed tooltip is announced');
  assert($('memories-query').value === 'draft-kept', 'collapse preserves form content');
  assert(localStorage.getItem('remember-me.sidebar.collapsed.v1') === 'true', 'collapse persisted');
  memBtn.click();
  await settle();
  assert($('app-shell').classList.contains('sidebar-collapsed'), 'menu click does not toggle sidebar');
  nav.click();
  await settle();
  assert(!$('app-shell').classList.contains('sidebar-collapsed'), 'expand restores');
  assert($('nav-tooltip').hidden && !memBtn.hasAttribute('aria-describedby'), 'expanding hides tooltip and clears description');
  assert(localStorage.getItem('remember-me.sidebar.collapsed.v1') === 'false', 'expand persisted');

  // Debug styling must not change the nav click target's layout.
  const navTop = nav.getBoundingClientRect().top;
  $('btn-debug').click();
  assert(document.body.classList.contains('debug-ids'), 'debug labels enabled');
  assert(nav.getBoundingClientRect().top === navTop, 'debug mode preserves nav layout');
  assert($('btn-debug').querySelector('svg'), 'debug toggle preserves its icon');
  $('btn-debug').click();

  nav.focus();
  nav.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
  assert($('app-shell').classList.contains('sidebar-collapsed'), 'focused nav toggles with Enter');
  nav.dispatchEvent(new KeyboardEvent('keydown', {key: ' ', bubbles: true}));
  assert(!$('app-shell').classList.contains('sidebar-collapsed'), 'focused nav toggles with Space');

  // Keyboard activation of the collapsed nav button.
  memBtn.focus();
  memBtn.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
  memBtn.click();
  await settle();
  assert($('page-memories').classList.contains('active'), 'keyboard/nav activation switches page');

  await runMemoriesFiltersRegression();
  await runMemoriesSearchRegression();
  await runMemoriesAcceptanceRegression();
  result.textContent = 'PASS: real /admin/console entry; nav switch; browse, pagination, literal, semantic, exact jump, selection retention, low-frequency, stale response; common filters, candidates, focus and reset; special characters, safe actions and errors; shutdown controls; model bootstrap/progress/proxy/retry; desktop shell status/layout/collapse/scroll';
  }
} catch (e) { result.textContent = 'FAIL: ' + e.stack; }

finally {
  const metrics=document.createElement('pre'); metrics.id='smoke-metrics';
  metrics.textContent=JSON.stringify({width:innerWidth,height:innerHeight,dpr:devicePixelRatio});
  metrics.hidden=true; document.body.append(metrics);
}
