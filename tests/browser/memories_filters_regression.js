// Runs against the real browser modules; overrides only fetch for failure/race cases.
async function runMemoriesFiltersRegression() {
  if (!$('memories-reset').hidden) $('memories-reset').click();
  await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false' && $('memories-reset').hidden,
    'start from unfiltered browse');
  const escape = () => document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  const chooseCategory = value => {
    const node = [...document.querySelectorAll('.memory-category-option input')].find(node => node.dataset.value === value);
    assert(node && !node.disabled, 'category selectable: ' + value); node.click();
  };
  const enterKeyword = async value => {
    const input = $('memories-keywords-query'); input.value = value; fire(input, 'input');
    await waitFor(() => [...document.querySelectorAll('[data-keyword]')].some(node => node.dataset.keyword.includes(value.toLowerCase())), 'keyword suggestions ' + value);
  };
  const addKeyword = value => {
    const node = [...document.querySelectorAll('[data-keyword]')].find(node => node.dataset.keyword === value);
    assert(node && !node.disabled, 'keyword selectable: ' + value); node.focus(); node.click();
  };
  assert(getComputedStyle($('memories-reset')).display === 'none', 'Reset hidden without filters');
  $('memories-pagination').querySelector('[aria-label="' + t('memories.pagination.next') + '"]').click();
  await waitFor(() => $('memories-pagination').textContent.includes('21–40'), 'filter starts from second page');
  $('memories-categories').click();
  assert(document.activeElement === $('memories-categories-query'), 'category opening focuses search');
  chooseCategory('design');
  await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:32}), 'real category filter count');
  assert($('memories-categories').classList.contains('has-filter') && $('memories-reset').classList.contains('has-filter'),
    'applied category and Reset highlighted');
  assert($('memories-pagination').textContent.includes('1–20'), 'filter resets page');
  chooseCategory('bugfix');
  await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:63}), 'real category OR');
  const categoryPreview = $('memories-selected-categories');
  assert(categoryPreview.children.length === 2 && categoryPreview.textContent.includes('design')
    && categoryPreview.textContent.includes('bugfix'), 'selected categories visible beside trigger');
  assert([...categoryPreview.children].every(node => node.id), 'category preview controls have IDs');
  const removeDesign = [...categoryPreview.children].find(node => node.title === 'design');
  removeDesign.focus(); removeDesign.click();
  await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:31}), 'category chip removes applied filter');
  assert(categoryPreview.children.length === 1 && categoryPreview.contains(document.activeElement),
    'category removal preserves remaining chip focus');
  $('memories-categories').click(); chooseCategory('design');
  await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:63}), 'restore category OR');
  escape(); assert(document.activeElement === $('memories-categories'), 'category Escape focus');
  S.resetCalls(); $('memories-keywords').click(); await settle(300);
  assert(!S.calls.some(call => call.url === '/api/keywords/query'), 'empty keyword input sends no requests');
  await enterKeyword('CACHE'); addKeyword('cache');
  await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:1}), 'real cross-dimension AND');
  addKeyword('cache clear');
  await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:2}), 'real keyword OR');
  const input = $('memories-keywords-query'); assert(input.value === 'CACHE', 'adding keeps query');
  escape();
  $('memories-categories').click(); chooseCategory('bugfix');
  await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:1}), 'AND narrows keyword OR');
  escape();
  $('memories-categories').click();
  $('memories-namespace').value = 'a'; fire($('memories-namespace'), 'change');
  assert(!document.querySelector('.memory-filter-popover'), 'namespace change closes the open filter popover');
  await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'namespace filter applied');
  assert($('memories-namespace-control').classList.contains('has-filter') && $('memories-keywords').classList.contains('has-filter'),
    'applied namespace and keywords highlighted');
  assert($('memories-selected-preview').textContent.includes('cache clear'), 'scope preserves selected keywords');
  let bodies = [];
  S.setOverride((url, init) => {
    if (url === '/api/keywords/query') bodies.push(JSON.parse(init.body));
    return undefined;
  });
  $('memories-keywords').click(); await enterKeyword('cache');
  assert(bodies.at(-1).namespace === 'a' && bodies.at(-1).categories.join() === 'design', 'candidate scope uses namespace/categories');
  escape();
  $('memories-sort').value = 'created_at'; fire($('memories-sort'), 'change');
  $('memories-page-size').value = '10'; fire($('memories-page-size'), 'change');
  await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'before reset');
  $('memories-keywords').click();
  S.resetCalls(); $('memories-reset').click();
  assert(!document.querySelector('.memory-filter-popover') && document.activeElement === $('memories-namespace'), 'Reset closes popup and restores focus');
  await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:63}), 'real reset');
  assert(['memories-namespace-control','memories-categories','memories-keywords','memories-reset']
    .every(id => !$(id).classList.contains('has-filter')), 'Reset clears all applied filter highlights');
  assert(S.calls.filter(call => call.url.startsWith('/api/memories/page?')).length === 1, 'Reset makes one request');
  assert($('memories-sort').value === 'created_at' && $('memories-page-size').value === '10', 'Reset preserves sort and size');
  $('memories-sort').value = 'updated_at'; fire($('memories-sort'), 'change');
  $('memories-page-size').value = '20'; fire($('memories-page-size'), 'change');
  await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'restore browse defaults');

  const sample = await S.realFetch('/api/stats').then(response => response.json());
  let failStats = true, releaseStats;
  S.setOverride(url => url === '/api/stats' ? (failStats ? json({detail: 'Stats temporarily offline'}, 500)
    : releaseStats === undefined ? new Promise(resolve => { releaseStats = resolve; }) : json(sample)) : undefined);
  $('btn-refresh').click();
  $('memories-categories').click();
  await waitFor(() => $('memories-categories-popover').textContent.includes('Stats temporarily offline'), 'category refresh failure');
  assert(document.querySelectorAll('.memory-category-option').length > 0, 'category refresh failure retains candidates');
  failStats = false;
  [...$('memories-categories-popover').querySelectorAll('button')].find(node => node.textContent === 'Retry').click();
  await waitFor(() => releaseStats, 'pending stats refresh');
  S.resetCalls(); $('btn-refresh').click(); $('memories-categories').click();
  await waitFor(() => !$('memories-categories-popover').textContent.includes('正在加载分类'), 'latest stats response');
  releaseStats(json({...sample, category_counts: {'obsolete-category': 1}})); await settle(50);
  assert(!$('memories-categories-popover').textContent.includes('obsolete-category'), 'old Stats response ignored');
  assert(S.calls.filter(call => call.url.startsWith('/api/memories/page?')).length === 1, 'registry updates do not refetch memories');
  escape();

  let releaseKeyword, failMore = true;
  const words = Array.from({length: 21}, (_, i) => ({keyword: i === 0 ? 'a,b' : 'pick' + String(i).padStart(2, '0'), count: i + 1}));
  S.setOverride((url, init) => {
    if (url === '/api/keywords/query') {
      const body = JSON.parse(init.body); bodies.push(body);
      if (body.queries[0] === 'old') return new Promise(resolve => { releaseKeyword = resolve; });
      if (body.offset && failMore) return json({detail: 'More failed'}, 500);
      const items = body.offset ? [words[19], ...words.slice(body.offset, body.offset + 20)]
        : words.slice(body.offset, body.offset + 20);
      return json({results: [{items, total: 21}]});
    }
    return undefined;
  });
  $('memories-keywords').click();
  inputQuery('old'); await waitFor(() => releaseKeyword, 'pending old suggestions');
  inputQuery('pick');
  releaseKeyword(json({results: [{items: [{keyword: 'stale-word', count: 1}], total: 1}]}));
  await settle(50);
  assert(!$('memories-keywords-popover').textContent.includes('stale-word'), 'old response ignored during debounce');
  await waitFor(() => document.querySelectorAll('[data-keyword]').length === 20, 'first suggestion page');
  addKeyword('a,b');
  await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'comma filter applied');
  const pageCall = S.calls.filter(call => call.url.startsWith('/api/memories/page?')).at(-1);
  assert(new URL(pageCall.url, location.origin).searchParams.getAll('keywords').join('|') === 'a,b', 'comma keyword remains one parameter');
  assert($('memories-list').textContent.includes(t('memories.clearFilters')), 'filtered empty result offers reset');
  $('memories-keywords-more').click();
  await waitFor(() => $('memories-keywords-popover').textContent.includes('More failed'), 'more failure');
  assert(document.querySelectorAll('[data-keyword]').length === 20, 'failed more retains suggestions');
  failMore = false;
  [...$('memories-keywords-popover').querySelectorAll('button')].find(node => node.textContent === 'Retry').click();
  await waitFor(() => document.querySelectorAll('[data-keyword]').length === 21, 'retry more same offset');
  for (const [index, word] of words.slice(1, 20).entries()) {
    addKeyword(word.keyword);
    if (index === 3) {
      assert($('memories-selected-preview').children.length === 5 && $('memories-keywords').textContent === 'Keywords ▾',
        'five keywords all visible without overflow count');
    }
    if (index === 4) {
      assert($('memories-selected-preview').children.length === 5 && $('memories-keywords').textContent === 'Keywords · +1 ▾'
        && ![...$('memories-selected-preview').children].some(node => node.title === word.keyword),
        'sixth keyword hidden and counted once');
      [...document.querySelectorAll('.memory-filter-selected button')].find(node => node.title === word.keyword).click();
      assert($('memories-selected-preview').children.length === 5 && $('memories-keywords').textContent === 'Keywords ▾',
        'removing sixth keyword clears overflow count');
      addKeyword(word.keyword);
    }
  }
  assert([...document.querySelectorAll('[data-keyword]')].every(node => node.disabled), '20 keyword cap disables additions');
  assert($('memories-selected-preview').children.length === 5 && $('memories-keywords').textContent.includes('+15'), 'compact selected preview');
  const firstSelected = document.querySelector('.memory-filter-selected button');
  firstSelected.focus(); firstSelected.click();
  assert(![...document.querySelectorAll('[data-keyword]')].find(node => node.dataset.keyword === 'pick20').disabled, 'removal permits additions');
  assert(document.activeElement.closest('.memory-filter-selected'), 'removing selected item restores nearby focus');
  escape();
  const candidateNames = Array.from({length: 21}, (_, i) => 'category' + i);
  S.setOverride(url => url === '/api/stats' ? json({...sample, category_counts: Object.fromEntries(candidateNames.map(value => [value, 0]))}) : undefined);
  $('btn-refresh').click(); $('memories-categories').click();
  await waitFor(() => document.querySelectorAll('.memory-category-option').length === 21, 'category cap fixture');
  candidateNames.slice(0, 20).forEach((value, index) => {
    chooseCategory(value);
    if (index === 2) {
      assert(categoryPreview.children.length === 3 && $('memories-categories').textContent === 'Categories ▾',
        'three categories all visible without overflow count');
    }
    if (index === 3) {
      assert(categoryPreview.children.length === 3 && $('memories-categories').textContent === 'Categories · +1 ▾'
        && ![...categoryPreview.children].some(node => node.title === value), 'fourth category hidden and counted once');
      chooseCategory(value);
      assert(categoryPreview.children.length === 3 && $('memories-categories').textContent === 'Categories ▾',
        'removing fourth category clears overflow count');
      chooseCategory(value);
    }
  });
  assert([...document.querySelectorAll('.memory-category-option input')].find(node => node.dataset.value === 'category20').disabled, '20 category cap');
  chooseCategory('category0');
  assert(![...document.querySelectorAll('.memory-category-option input')].find(node => node.dataset.value === 'category20').disabled, 'category removal permits additions');
  S.setOverride(url => url === '/api/stats' ? json(sample) : undefined);
  $('btn-refresh').click(); $('memories-categories').click();
  await waitFor(() => $('memories-categories-popover').textContent.includes('当前候选中不可用'), 'selected unavailable category retained');
  escape();
  S.resetCalls(); $('memories-reset').click();
  await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:63}), 'reset cap fixture');
  $('memories-keywords').click(); inputQuery('word');
  document.querySelector('.nav button[data-page="stats"]').click(); await settle(300);
  assert(!document.querySelector('.memory-filter-popover'), 'leaving page closes popup');
  assert(!S.calls.some(call => call.url === '/api/keywords/query' && JSON.parse(call.body).limit === 20), 'leaving cancels candidate debounce');
  document.querySelector('.nav button[data-page="memories"]').click();
  S.reset(); await waitFor(() => $('memories-count').textContent === t('memories.count.browse',{total:63}), 'filters restored to default');

  function inputQuery(value) {
    $('memories-keywords-query').value = value; fire($('memories-keywords-query'), 'input');
  }
}
