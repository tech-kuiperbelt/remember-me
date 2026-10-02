async function runMemoriesSearchRegression() {
  const query = value => { $('memories-query').value = value; fire($('memories-query'), 'input'); };
  const search = value => { query(value); submit($('memories-search-form')); };
  const ready = () => waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'search ready');
  const searchCalls = () => S.calls.filter(call => call.url === '/api/memories/search');
  const pageCalls = () => S.calls.filter(call => call.url.startsWith('/api/memories/page?'));
  document.querySelector('.nav button[data-page="memories"]').click(); await ready();
  if (!$('memories-search-clear').disabled) $('memories-search-clear').click();
  if (!$('memories-reset').hidden) $('memories-reset').click(); await ready();
  const records = Array.from({length:25}, (_, i) => ({id: 'search-' + i, text: 'Search record ' + i,
    summary: 'Search record ' + i, namespace: 'a', score: 1 - i / 100, metadata: {},
    updated_at: '2026-01-01T00:00:00.' + String(100 + i).padStart(6, '0') + '+00:00'}));
  records[23].updated_at = '2026-01-01T08:00:00.000124+08:00'; // same instant as record 24
  const mapped = {config:{}, keywords:[{input:'<locking>', match_type:'semantic', matches:[
    {value:'optimistic-lock',accepted:true}, {value:'lock',accepted:true}]}],
    categories:[{input:'absent',match_type:'none',matches:[]},{input:'design',match_type:'exact',matches:[]}]};
  S.setOverride(url => url === '/api/memories/search' ? json({items:records, filter_resolution:mapped}) : undefined);
  $('memories-k').value = '25'; S.resetCalls(); query('A'); await settle(30); assert(searchCalls().length === 0, 'typing is not search');
  submit($('memories-search-form')); await ready();
  assert(searchCalls().length === 1 && JSON.parse(searchCalls()[0].body).include_resolution === true, 'one envelope request');
  assert($('memories-count').textContent === '本次搜索 25 条 · 最多 25 条', 'Top-K count not full inventory');
  assert($('memories-list').textContent.includes('0.99 · Semantic'), 'raw cosine not probability');
  assert($('memories-filter-resolution').textContent.includes('<locking> → optimistic-lock、lock'), 'semantic accepted values shown');
  assert($('memories-filter-resolution').textContent.includes('absent 未找到匹配值'), 'none explained');
  assert(!$('memories-filter-resolution').textContent.includes('design') && !$('memories-filter-resolution').querySelector('locking'), 'exact hidden and text safe');
  $('memories-page-size').value = '10'; fire($('memories-page-size'), 'change');
  $('memories-pagination').querySelector('[aria-label="下一页"]').click();
  assert($('memories-pagination').textContent.includes('11–20'), 'local search page');
  $('memories-sort').value = 'updated_at'; fire($('memories-sort'), 'change');
  assert($('memories-list').querySelector('.memory-row').dataset.memoryId === 'search-23', 'microsecond order and API tie stability');
  assert(searchCalls().length === 1, 'paging and sorting avoid repeated inference');
  query('B'); S.resetCalls(); $('btn-refresh').click(); await ready();
  assert(JSON.parse(searchCalls()[0].body).query === 'A' && $('memories-query').value === 'B', 'Refresh uses submitted query and retains draft');
  S.resetCalls(); $('memories-namespace').value = 'a'; fire($('memories-namespace'), 'change'); await ready();
  assert(JSON.parse(searchCalls()[0].body).query === 'A' && JSON.parse(searchCalls()[0].body).namespace === 'a', 'search scope uses submitted query');
  $('memories-categories').click();
  [...document.querySelectorAll('.memory-category-option input')].find(node => node.dataset.value === 'design').click(); await ready();
  assert(JSON.parse(searchCalls().at(-1).body).categories.join('|') === 'design', 'Search shares category filter');
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  $('memories-keywords').click(); $('memories-keywords-query').value = 'cache'; fire($('memories-keywords-query'),'input');
  await waitFor(() => document.querySelector('[data-keyword="cache"]'), 'search keyword candidate');
  document.querySelector('[data-keyword="cache"]').click(); await ready();
  assert(JSON.parse(searchCalls().at(-1).body).keywords.join('|') === 'cache', 'Search shares keyword filter');
  S.resetCalls(); $('memories-reset').click(); await ready();
  assert(searchCalls().length === 1 && JSON.parse(searchCalls()[0].body).query === 'A' && $('memories-query').value === 'B', 'Reset clears filters and preserves both queries');
  $('memories-k').value = '25'; submit($('memories-advanced-form')); await ready();
  assert(JSON.parse(searchCalls().at(-1).body).query === 'A' && JSON.parse(searchCalls().at(-1).body).k === 25, 'Apply uses submitted query');
  for (const bad of ['', '0', '101', '1.5']) {
    $('memories-k').value = bad; S.resetCalls(); submit($('memories-advanced-form'));
    assert($('memories-k').getAttribute('aria-invalid') === 'true' && !searchCalls().length, 'invalid K rejected: ' + bad);
  }
  $('memories-k').value = '25'; $('memories-threshold').value = '2'; S.resetCalls(); submit($('memories-advanced-form'));
  assert($('memories-threshold').getAttribute('aria-invalid') === 'true' && !searchCalls().length, 'invalid threshold rejected');
  $('memories-threshold').value = '0'; submit($('memories-advanced-form')); await ready();

  let release;
  S.setOverride(url => url === '/api/memories/search' ? new Promise(resolve => {release = resolve;}) : undefined);
  search('slow'); await waitFor(() => release, 'slow search started');
  $('memories-page-size').value = '20'; fire($('memories-page-size'), 'change');
  $('memories-sort').value = 'relevance'; fire($('memories-sort'), 'change');
  release(json({items:records, filter_resolution:null})); await ready();
  assert($('memories-list').querySelectorAll('.memory-row').length === 20, 'pending search uses latest page size');
  assert($('memories-list').querySelector('.memory-row').dataset.memoryId === 'search-0', 'pending search uses latest sort');
  let stale;
  S.setOverride(url => url === '/api/memories/search' ? new Promise(resolve => {stale = resolve;}) : undefined);
  search('stale'); await waitFor(() => stale, 'stale search started'); $('memories-search-clear').click(); await ready();
  stale(json({detail:'old failure'},500)); await settle(20);
  assert($('memories-list').querySelector('.memory-row') && !$('memories-filter-resolution').textContent, 'old failure cannot overwrite Browse');
  S.reset();
  $('memories-advanced').open = true;
  $('memories-updated-after').value = '2020-01-01T00:00:00';
  $('memories-updated-before').value = '2019-01-01T00:00:00'; S.resetCalls(); submit($('memories-advanced-form'));
  assert($('memories-updated-before').getAttribute('aria-invalid') === 'true' && !pageCalls().length, 'invalid time range rejected');
  $('memories-updated-before').value = '2030-01-01T00:00:00'; submit($('memories-advanced-form')); await ready();
  const params = new URL(pageCalls().at(-1).url, location.origin).searchParams;
  assert(params.get('updated_after') === new Date('2020-01-01T00:00:00').toISOString(), 'local date converted to UTC');
  $('memories-updated-after').value = '2021-01-01T00:00:00'; // unapplied draft
  S.setOverride(url => url === '/api/memories/search' ? json({items:records,filter_resolution:null}) : undefined);
  S.resetCalls(); search('dates'); await ready();
  assert(!('updated_after' in JSON.parse(searchCalls()[0].body)) && $('memories-updated-after').disabled, 'search excludes retained dates');
  S.resetCalls(); $('memories-search-clear').click(); await ready();
  assert(new URL(pageCalls()[0].url,location.origin).searchParams.get('updated_after') === params.get('updated_after'), 'return Browse uses applied time');
  assert(new Date($('memories-updated-after').value).getTime() === new Date('2021-01-01T00:00:00').getTime(), 'unapplied date retained');
  S.resetCalls(); $('memories-reset').click(); await ready();
  assert(pageCalls().length === 1 && !$('memories-updated-after').value, 'Reset clears applied and draft date once');
  // A late success cannot restore mappings after clear; invalid retained K remains editable.
  let lateSuccess;
  S.setOverride(url => url === '/api/memories/search' ? new Promise(resolve => {lateSuccess = resolve;}) : undefined);
  search('late success'); await waitFor(() => lateSuccess, 'late success started');
  $('memories-k').value = '0'; $('memories-search-clear').click(); await ready();
  lateSuccess(json({items:records,filter_resolution:mapped})); await settle(25);
  assert(!$('memories-filter-resolution').textContent && !$('memories-list').querySelector('.memory-search-score'), 'late success ignored');
  S.resetCalls(); search('correct draft');
  assert(!searchCalls().length && !$('memories-k').disabled && document.activeElement === $('memories-k'), 'invalid retained draft can be corrected from Browse');
  $('memories-k').value = '25';
  S.reset();

  // Error does not revive old collection through local sorting.
  S.setOverride(url => url === '/api/memories/search' ? json({detail:'search unavailable'},500) : undefined);
  search('failure'); await ready(); $('memories-sort').value = 'updated_at'; fire($('memories-sort'),'change');
  assert($('memories-list').textContent.includes('search unavailable') && !$('memories-list').querySelector('.memory-row'), 'error remains after local sort');
  S.setOverride(url => url === '/api/memories/search' ? json({items:[],filter_resolution:mapped}) : undefined);
  [...$('memories-list').querySelectorAll('button')].find(b => b.textContent === 'Retry').click(); await ready();
  assert($('memories-filter-resolution').textContent.includes('未找到匹配值'), 'empty search still explains mapping');
  assert([...$('memories-list').querySelectorAll('button')].some(b => b.textContent === '清空搜索'), 'pure empty search has clear action');
  assert(!$('nav-search') && !$('page-search'), 'old search page removed');
  const {switchPage} = await import('/admin/console/static/js/router.js');
  assert(switchPage('search') === false && $('page-memories').classList.contains('active'), 'retired route preserves page');
  S.reset(); $('memories-search-clear').click(); await ready();
  $('memories-k').value = '10'; $('memories-threshold').value = '0'; $('memories-advanced').open = false;
}
