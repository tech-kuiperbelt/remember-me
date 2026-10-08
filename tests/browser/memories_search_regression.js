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
  assert($('memories-list').textContent.includes('Semantic 0.99'), 'raw cosine not probability');
  const regionIds = ['memory-row-1', 'memory-row-1-open', 'memory-row-1-title',
    'memory-row-1-summary', 'memory-row-1-footer', 'memory-row-1-score', 'memory-row-1-date'];
  const geometry = id => {
    const box = $(id).getBoundingClientRect();
    return [box.x, box.y, box.width, box.height];
  };
  const originalRegions = regionIds.map(geometry), originalList = $('memories-list').textContent;
  $('btn-debug').click();
  assert(regionIds.every((id, i) => JSON.stringify(geometry(id)) === JSON.stringify(originalRegions[i])), 'Debug preserves row region geometry');
  assert(getComputedStyle($('memory-row-1-open')).position === 'absolute', 'Debug preserves row overlay positioning');
  $('memory-row-1-title').dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
  assert(document.querySelector('.debug-id-label').textContent === 'memory-row-1-title', 'Debug identifies row title');
  $('memory-row-1-open').click();
  assert($('detail-modal').style.display === 'none' && $('memories-list').textContent === originalList, 'Debug inspection does not open detail or change content');
  const modeBeforeDebug = document.querySelector('#memories-search-mode [aria-checked="true"]').id;
  for (const key of ['ArrowRight', 'ArrowLeft', 'Home', 'End', 'Enter', ' ']) {
    $('memories-mode-semantic').dispatchEvent(new KeyboardEvent('keydown', {key, bubbles:true, cancelable:true}));
  }
  assert(document.querySelector('#memories-search-mode [aria-checked="true"]').id === modeBeforeDebug
    && $('memories-list').textContent === originalList, 'Debug blocks mode keyboard actions and rerender');
  $('memories-query').focus();
  assert(!$('memories-query').dispatchEvent(new InputEvent('beforeinput', {inputType:'insertText',data:'x',bubbles:true,cancelable:true})), 'Debug prevents editing before value changes');
  assert(!$('memories-namespace').dispatchEvent(new PointerEvent('pointerdown', {button:0,bubbles:true,cancelable:true})), 'Debug blocks native select pointer activation');
  const callsBeforeDebugSubmit = S.calls.length;
  submit($('memories-search-form'));
  assert(S.calls.length === callsBeforeDebugSubmit, 'Debug blocks form submit');
  assert($('memories-query').dispatchEvent(new KeyboardEvent('keydown', {key:'Tab',bubbles:true,cancelable:true})), 'Debug preserves native Tab navigation');
  assert($('memories-query').dispatchEvent(new KeyboardEvent('keydown', {key:'+',ctrlKey:true,bubbles:true,cancelable:true})), 'Debug preserves browser shortcuts');
  document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true,cancelable:true}));
  assert(!document.body.classList.contains('debug-ids') && document.activeElement === $('btn-debug'), 'Escape exits Debug and restores button focus');
  assert(!document.querySelector('.debug-id-label') && !$('memories-list').querySelector('.debug-focus'), 'Debug cleanup removes overlay and selection');
  assert($('memories-filter-resolution').textContent.includes('<locking> → optimistic-lock、lock'), 'semantic accepted values shown');
  assert($('memories-filter-resolution').textContent.includes('absent 未找到匹配值'), 'none explained');
  assert(!$('memories-filter-resolution').textContent.includes('design') && !$('memories-filter-resolution').querySelector('locking'), 'exact hidden and text safe');
  $('memories-page-size').value = '10'; fire($('memories-page-size'), 'change');
  $('memories-pagination').querySelector('[aria-label="下一页"]').click();
  assert($('memories-pagination').textContent.includes('11–20'), 'local search page');
  assert($('memories-sort').disabled && $('memories-sort').options.length === 1, 'search sort fixed to relevance');
  assert($('memories-list').querySelector('.memory-row').dataset.memoryId === 'search-10', 'server order retained on local page');
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

  // Modes retain independent raw drafts and applied parameters; public scope is shared.
  const modeButton = name => $('memories-mode-' + name);
  const allSearchCalls = () => S.calls.filter(c => c.url.startsWith('/api/memories/search'));
  const lastBody = () => JSON.parse(allSearchCalls().at(-1).body);
  const bm25 = records.map((row, i) => ({...row, score: i === 0 ? 1e-9 : 2.123456789}));
  S.setOverride(url => url === '/api/memories/search' ? json({items:records,filter_resolution:mapped})
    : url.startsWith('/api/memories/search/') ? json(bm25) : undefined);
  $('memories-updated-after').value = '2020-01-01T00:00:00'; submit($('memories-advanced-form')); await ready();
  $('memories-namespace').value = 'a'; fire($('memories-namespace'),'change'); await ready();
  $('memories-categories').click();
  [...document.querySelectorAll('.memory-category-option input')].find(n => n.dataset.value === 'design').click(); await ready();
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  $('memories-keywords').click(); $('memories-keywords-query').value='cache'; fire($('memories-keywords-query'),'input');
  await waitFor(()=>document.querySelector('[data-keyword="cache"]'),'mode keyword');
  document.querySelector('[data-keyword="cache"]').click(); await ready();
  $('memories-k').value='bad'; $('memories-threshold').value='NaN';
  $('memories-updated-after').value='2021-01-01T00:00:00'; query('unsubmitted');
  S.resetCalls(); modeButton('lexical').click(); await ready();
  assert(allSearchCalls()[0].url.endsWith('/lexical') && lastBody().query==='A' && lastBody().k===10
    && !('min_score' in lastBody()) && !('threshold' in lastBody()) && !('include_resolution' in lastBody()), 'Lexical route and default applied parameters');
  assert(lastBody().namespace==='a' && lastBody().categories[0]==='design' && lastBody().keywords[0]==='cache'
    && lastBody().updated_after===new Date('2020-01-01T00:00:00').toISOString(), 'mode carries applied public filters and date');
  assert($('memories-query').value==='unsubmitted' && $('memories-k').value==='10' && $('memories-threshold').value==='', 'switch preserves query and restores mode draft');
  assert($('memories-list').textContent.includes('FTS 0.000')
    && $('memories-list').querySelector('.memory-search-score').title === 'FTS 1e-9'
    && !$('memories-filter-resolution').textContent, 'FTS three decimals, full score tooltip and mappings cleared');
  $('memories-k').value='13'; $('memories-threshold').value='2e0'; submit($('memories-advanced-form')); await ready();
  assert(lastBody().query==='A' && lastBody().k===13 && lastBody().min_score===2, 'BM25 scientific notation above one and Apply query');
  for(const bad of ['-1','Infinity','NaN','1e999','0x10']) {
    $('memories-threshold').value=bad; S.resetCalls(); submit($('memories-advanced-form'));
    assert(!allSearchCalls().length && document.activeElement===$('memories-threshold'), 'invalid BM25 focuses field: '+bad);
  }
  $('memories-threshold').value='-1'; S.resetCalls(); modeButton('anchor').click(); await ready();
  assert(allSearchCalls()[0].url.endsWith('/anchor') && lastBody().k===10 && !('min_score' in lastBody()), 'Anchor defaults isolated');
  assert($('memory-row-1-score').textContent === 'Anchor 0.000'
    && $('memory-row-1-score').title === 'Anchor 1e-9', 'Anchor three decimals with full score tooltip');
  $('memories-k').value='7'; $('memories-threshold').value='1e-8'; submit($('memories-advanced-form')); await ready();
  assert(lastBody().k===7 && lastBody().min_score===1e-8, 'Anchor independent applied values');
  modeButton('lexical').click(); await ready();
  assert(lastBody().k===13 && lastBody().min_score===2 && $('memories-threshold').value==='-1', 'invalid raw draft retained without application');
  $('memories-threshold').value=''; submit($('memories-advanced-form')); await ready();
  assert(!('min_score' in lastBody()), 'blank BM25 floor omitted');
  $('memories-reset').click(); await ready();
  assert(lastBody().k===13 && lastBody().query==='A' && !lastBody().namespace && !$('memories-updated-after').value, 'Reset retains mode parameters and query');
  modeButton('semantic').click(); await ready();
  assert(lastBody().k===25 && lastBody().threshold===0 && $('memories-k').value==='bad' && $('memories-threshold').value==='NaN', 'Semantic applied and invalid draft retained');
  $('memories-k').value='25'; $('memories-threshold').value='0';
  // Response contracts are validated before rows render; Retry keeps applied mode parameters.
  for (const name of ['lexical','anchor']) {
    S.setOverride(url => url.endsWith('/'+name) ? json({items:bm25}) : undefined);
    modeButton(name).click(); await ready();
    assert($('memories-list').textContent.includes('Invalid search response'), 'terms envelope rejected: '+name);
    S.setOverride(url => url.endsWith('/'+name) ? json([{...bm25[0],score:'1e-9'}]) : undefined);
    [...$('memories-list').querySelectorAll('button')].find(b=>b.textContent==='Retry').click(); await ready();
    assert($('memories-list').textContent.includes('Invalid search response'), 'non-number score rejected: '+name);
    S.setOverride(url => url.endsWith('/'+name) ? json(bm25) : undefined);
    S.resetCalls(); [...$('memories-list').querySelectorAll('button')].find(b=>b.textContent==='Retry').click(); await ready();
    assert(allSearchCalls().length===1 && lastBody().query==='A' && lastBody().k===(name==='lexical'?13:7), 'Retry keeps terms mode applied values');
  }
  S.setOverride(url => url === '/api/memories/search' ? json({items:records,filter_resolution:null}) : undefined);
  modeButton('semantic').click(); await ready();
  // Old mode success/failure cannot replace a new mode or restore mappings.
  for(const failure of [false,true]) {
    let old;
    S.setOverride(url => url === '/api/memories/search' ? new Promise(resolve=>{old=resolve;})
      : url.startsWith('/api/memories/search/') ? json(bm25) : undefined);
    $('btn-refresh').click(); await waitFor(()=>old,'pending mode response');
    modeButton('anchor').click();
    assert(!$('memories-list').querySelector('.memory-search-score'), 'mode loading clears old scores');
    await ready(); old(failure ? json({detail:'stale mode failure'},500) : json({items:records,filter_resolution:mapped})); await settle(25);
    assert($('memories-list').textContent.includes('Anchor 0.000') && !$('memories-filter-resolution').textContent, 'stale mode response ignored');
    S.setOverride(url => url === '/api/memories/search' ? json({items:records,filter_resolution:null}) : undefined);
    modeButton('semantic').click(); await ready();
  }
  $('memories-search-clear').click(); await ready(); S.resetCalls();
  const key = (name, value) => modeButton(name).dispatchEvent(new KeyboardEvent('keydown',{key:value,bubbles:true,cancelable:true}));
  modeButton('semantic').focus(); key('semantic','ArrowLeft');
  assert(modeButton('anchor').getAttribute('aria-checked')==='true' && document.activeElement===modeButton('anchor'), 'arrow wrap and focus');
  key('anchor','Home'); key('semantic','End'); key('anchor','ArrowRight'); key('semantic',' '); key('semantic','Enter');
  assert(!allSearchCalls().length && !pageCalls().length, 'Browse switches and same selection do not request');
  assert([...$('memories-search-mode').children].filter(n=>n.tabIndex===0).length===1, 'single Tab stop');
  $('btn-debug').click();
  for(const id of ['memories-search-mode','memories-mode-semantic','memories-mode-lexical','memories-mode-anchor','memories-threshold-label','memories-threshold-help']) {
    $(id).dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
    assert(document.querySelector('.debug-id-label').textContent === id, 'Debug ID: '+id);
  }
  $('btn-debug').click();
  S.setOverride(url => url === '/api/memories/search' ? json({items:records,filter_resolution:null}) : undefined);

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
  assert(JSON.parse(searchCalls()[0].body).updated_after === new Date('2021-01-01T00:00:00').toISOString()
    && !$('memories-updated-after').disabled, 'Search applies date drafts');
  $('memories-updated-after').value = '2031-01-01T00:00:00'; S.resetCalls(); search('invalid dates');
  assert(!searchCalls().length && $('memories-updated-before').getAttribute('aria-invalid') === 'true', 'Search rejects invalid range');
  $('memories-updated-after').value = '2022-01-01T00:00:00'; S.resetCalls(); submit($('memories-advanced-form')); await ready();
  assert(JSON.parse(searchCalls()[0].body).query === 'dates' && JSON.parse(searchCalls()[0].body).updated_after === new Date('2022-01-01T00:00:00').toISOString(), 'Apply dates uses submitted query');
  let oldDate;
  S.setOverride(url => url === '/api/memories/search' ? new Promise(resolve => {oldDate = resolve;}) : undefined);
  $('memories-updated-after').value = '2021-06-01T00:00:00'; submit($('memories-advanced-form'));
  await waitFor(() => oldDate, 'old date search started');
  S.setOverride(url => url === '/api/memories/search' ? json({items:[],filter_resolution:null}) : undefined);
  $('memories-updated-after').value = '2022-01-01T00:00:00'; submit($('memories-advanced-form')); await ready();
  oldDate(json({items:records,filter_resolution:mapped})); await settle(25);
  assert(!$('memories-list').querySelector('.memory-row') && !$('memories-filter-resolution').textContent, 'old date response cannot replace latest window');
  assert([...$('memories-list').querySelectorAll('button')].some(node => node.textContent === 'Clear filters'), 'time-filtered empty search can clear filters');
  S.setOverride(url => url === '/api/memories/search' ? json({items:records,filter_resolution:null}) : undefined);
  $('memories-updated-after').value = '2023-01-01T00:00:00'; S.resetCalls(); $('btn-refresh').click(); await ready();
  assert(JSON.parse(searchCalls()[0].body).updated_after === new Date('2022-01-01T00:00:00').toISOString(), 'Refresh uses applied date');
  S.resetCalls(); $('memories-search-clear').click(); await ready();
  assert(new URL(pageCalls()[0].url,location.origin).searchParams.get('updated_after') === new Date('2022-01-01T00:00:00').toISOString(), 'return Browse uses applied time');
  assert(new Date($('memories-updated-after').value).getTime() === new Date('2023-01-01T00:00:00').getTime(), 'unapplied date retained');
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
