async function runAuditRegression(scenario) {
  await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'bootstrap');
  const event = {ts: '2026-10-11T01:00:00Z', tool: 'search_semantic', status: 'ok', duration_ms: 12.5,
    namespace: 'historical-space', params: {query: '<script>alert("x")</scr' + 'ipt>\n中文', namespace: 'historical-space'}, result: {items: [{id: 'a'}, {id: 'b'}], total: 2}, search_id: 'test-search'};
  const error = {...event, tool: 'update', status: 'error', error: {type: 'ValueError', message: 'Invalid input'}};
  await runAuditPresentationChecks();
  const layoutEvents = [event, error,
    {...event, tool:'report', duration_ms:0, params:{search_id:'search-example',ratings:[{m_id:'m1',rating:-10},{m_id:'m2',rating:0}]},result:{recorded:true,count:2}},
    {...event, tool:'search_meta', namespace:'很长的空间名称'.repeat(8),params:{keywords:['缓存','SQLite','性能','持久化'],categories:['design'],updated_after:'2026-10-01T00:00:00Z'},result:{items:[],total:0}},
    {...event, tool:'delete',params:{memory_id:'missing-memory'},result:{success:false}},
    {...event, tool:'ndjson_health_check',namespace:null,params:{trigger:'scheduled'},result:{in_sync:false}}];
  S.setOverride(url => url.startsWith('/api/audit?') ? json(scenario.includes('list') ? layoutEvents : [event, error]) : undefined);
  $('nav-audit').click();
  if (scenario === 'audit-layout-list-narrow') $('nav').click();
  await waitFor(() => $('audit-row-2'), 'audit rows');
  assert(!$('nav-audit').hidden, 'debug Audit visible');
  assert(!$('audit-list').textContent.includes('params:'), 'JSON stays in drawer');
  assert($('audit-row-1-summary').textContent.includes('中文'), 'search query appears in list');
  assert($('audit-row-1-namespace').textContent.includes('historical-space'), 'namespace has dedicated cell');
  assert($('audit-row-1-result').textContent === '结果ok', 'result shows call status only');
  assert($('audit-row-2-result').textContent === '结果error', 'error result shows status only');
  assert(!document.querySelector('[id^=audit-row-][id$=-outcome]'), 'business outcomes omitted from list');
  assert(!$('audit-row-1-summary').querySelector('script'), 'list summary is escaped');
  const width = $('audit-results').getBoundingClientRect().width;
  assert((getComputedStyle($('audit-list-header')).display !== 'none') === (width >= 1100), 'columns depend on container width');
  if (width >= 1100) {
    const header = $('audit-list-header').children;
    ['time', 'action', 'namespace', 'summary', 'result', 'duration'].forEach((name, index) => {
      assert(Math.abs(header[index].getBoundingClientRect().left - $('audit-row-1-' + name).getBoundingClientRect().left) <= 2, 'column aligned: ' + name);
    });
  }
  $('audit-namespace-select').click();
  const option = [...document.querySelectorAll('#audit-namespace-options [role=option]')].find(el => el.textContent === 'historical-space');
  assert(option, 'loaded historical namespace offered'); option.click();
  await waitFor(() => $('audit-list').getAttribute('aria-busy') === 'false', 'namespace query');
  assert(S.calls.some(call => call.url.includes('/api/audit?') && call.url.includes('namespace=historical-space')), 'selected namespace sent');
  $('audit-namespace').value = 'older-space';
  $('audit-namespace').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
  await waitFor(() => $('audit-list').getAttribute('aria-busy') === 'false', 'typed namespace');
  assert(S.calls.some(call => call.url.includes('namespace=older-space')), 'arbitrary namespace supported');
  const trigger = $('audit-row-1-detail'); trigger.focus(); trigger.click();
  assert($('audit-json').textContent === JSON.stringify(event, null, 2), 'pretty JSON retains all returned fields');
  assert(!$('audit-json').querySelector('script'), 'JSON is text');
  assert($('app-shell').inert && document.activeElement === $('btn-audit-close'), 'modal locks background and focuses close');
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true}));
  assert(document.activeElement === $('audit-json'), 'focus stays in drawer');
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}));
  assert(!$('app-shell').inert && document.activeElement === trigger, 'Escape restores focus');
  await waitFor(() => $('audit-modal').style.display === 'none', 'close motion');
  trigger.click(); $('btn-audit-close').click(); trigger.click();
  await settle(260); assert($('audit-modal').style.display === 'flex', 'rapid reopen survives cancelled motion');
  const ui = await import('/admin/console/static/js/ui.js'); ui.toggleDebug();
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}));
  assert(!document.body.classList.contains('debug-ids') && document.activeElement === $('btn-audit-close'), 'inspector Escape retains drawer focus');
  $('audit-modal').click(); await waitFor(() => $('audit-modal').style.display === 'none', 'backdrop closes');
  if (scenario === 'audit-functional') {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = query => query === '(prefers-reduced-motion: reduce)' ? {matches: true} : originalMatchMedia.call(window, query);
    trigger.click(); $('btn-audit-close').click();
    assert($('audit-modal').style.display === 'none', 'reduced motion closes immediately');
    window.matchMedia = originalMatchMedia;
    trigger.click();
    const router = await import('/admin/console/static/js/router.js');
    router.switchPage('memories');
    assert(!$('app-shell').inert && $('audit-modal').inert, 'page leave releases drawer background');
    $('nav-audit').click(); await waitFor(() => $('audit-row-2'), 'return to Audit');
    let resolveOld;
    S.setOverride(url => {
      if (!url.startsWith('/api/audit?')) return;
      if (url.includes('namespace=slow')) return new Promise(resolve => {resolveOld = resolve;});
      return json([]);
    });
    $('audit-namespace').value = 'slow'; $('btn-audit-refresh').click();
    await waitFor(() => resolveOld, 'slow request');
    $('audit-namespace').value = ''; $('btn-audit-refresh').click();
    await waitFor(() => $('audit-list').textContent.includes('暂无'), 'empty state');
    resolveOld(json([event])); await settle(); assert(!$('audit-row-1'), 'stale response ignored');
    S.setOverride(url => url.startsWith('/api/audit?') ? json({error: 'audit unavailable'}, 500) : undefined);
    $('btn-audit-refresh').click(); await waitFor(() => $('audit-list').textContent.includes('Error:'), 'error state');
    S.setOverride(url => url.startsWith('/api/audit?') ? json([event, error]) : undefined);
    $('btn-audit-refresh').click(); await waitFor(() => $('audit-row-2'), 'recovered');
  }
  assert(document.documentElement.scrollWidth <= innerWidth + 1, 'page fits viewport');
  if (scenario.startsWith('audit-layout-') && !scenario.includes('list')) {
    $('audit-row-1-detail').click(); await settle(260);
    assert($('audit-panel').getBoundingClientRect().width <= innerWidth + 1, 'drawer fits viewport');
  }
  result.textContent = 'PASS: Audit debug route, namespace picker, safe JSON drawer, focus, motion, states and stale requests';
}

async function runAuditPresentationChecks() {
  const {presentAudit: view} = await import('/admin/console/static/js/audit-presentation.js');
  const cases = [
    [{tool:'search_semantic',params:{query:'缓存',keywords:['a','b','c','d']},result:{items:[],total:0}}, 'summary', 'keywords: a, b, c · +1'],
    [{tool:'search_lexical',params:{query:'ERR_ID'},result:{items:[{}],total:10}}, 'outcome', '返回 1 条 · total 10'],
    [{tool:'search_anchor',result:{total:0}}, 'outcome', '返回 0 条'],
    [{tool:'search_meta',params:{categories:['design'],updated_after:'2026-10-01'}}, 'summary', 'updated_after: 2026-10-01'],
    [{tool:'search_meta',status:'ok',response_code:'namespace_not_found',result:{items:[]}}, 'outcome', 'namespace_not_found'],
    [{tool:'add',result:{id:'m1',created:false}}, 'outcome', '已存在'],
    [{tool:'add',memory_refs:[{id:'m2'}],params:{metadata:{anchors:['SomeAPI']}}}, 'summary', 'm2 · anchors: SomeAPI'],
    [{tool:'update',params:{memory_id:'m3'},result:{success:true},memory_refs:[{namespace_before:'old',namespace:'new'}]}, 'summary', 'old → new'],
    [{tool:'update',result:{success:false}}, 'outcome', '未更新'],
    [{tool:'delete',status:'ok',params:{memory_id:'m4'},result:{success:false}}, 'outcome', '未删除'],
    [{tool:'delete',result:{success:true}}, 'outcome', '已删除'],
    [{tool:'get_details',params:{memory_ids:['a','b','c','d']},result:{found:[{id:'a'}],missing:['b']}}, 'outcome', '找到 1 / 缺失 1'],
    [{tool:'get_details',result:{found:[]}}, 'outcome', '找到 0'],
    [{tool:'report',search_id:'s1',params:{ratings:[{m_id:'a',rating:-10},{m_id:'b',rating:0}]},result:{recorded:true,count:2}}, 'summary', 'a: -10, b: 0'],
    [{tool:'list_keywords',result:{results:[{query:'cache',total:12,items:[{}]},{query:'mem',total:0}]}}, 'outcome', 'cache: 12, mem: 0'],
    [{tool:'stats',result:{total:0}}, 'outcome', '0 条记忆'],
    [{tool:'memory_import',params:{filename:'input.jsonl'},result:{status:'running',import_id:'i1'}}, 'outcome', 'status: running'],
    [{tool:'memory_export',result:{records:0}}, 'outcome', 'records: 0'],
    [{tool:'online_recover_start',params:{filename:'recover.jsonl'},result:{job_id:'j1',phase:'waiting'}}, 'summary', 'job_id: j1'],
    [{tool:'online_recover',result:{phase:'done',count:3}}, 'outcome', 'phase: done'],
    [{tool:'maintenance_enter',result:{state:'maintenance'}}, 'outcome', 'maintenance'],
    [{tool:'maintenance_exit',result:{state:'running'}}, 'outcome', 'running'],
    [{tool:'ndjson_health_check',result:{in_sync:false}}, 'outcome', '不一致'],
    [{tool:'ndjson_compact',result:{outcome:'skipped',file_bytes:123}}, 'outcome', 'outcome: skipped'],
    [{tool:'model_config',result:{port:1314,language:'zh-CN',auto_download_model:false,proxy:{url:'secret'}}}, 'summary', 'auto_download_model: false'],
    [{tool:'model_prepare',result:{ready:false,status:'loading'}}, 'outcome', 'ready: false'],
    [{tool:'model_retry',result:{status:'loading'}}, 'summary', '重试准备模型'],
    [{tool:'embedding_reindex',params:{profile:{provider:'local',model:'m'},batch_size:8},result:{completed:0,total:12}}, 'outcome', 'completed: 0'],
    [{tool:'index_value_reindex',params:{profile:{model:'m'}}}, 'summary', 'model: m'],
    [{tool:'new_action',params:{query:'hello',path:'/private/file'},result:{count:0}}, 'summary', 'query: hello'],
    [{params:{query:{bad:'type'}},result:{count:-1}}, 'summary', '查看 JSON'],
    [{params:{namespace:'scope'}}, 'namespace', 'scope'],
    [{memory_refs:[{namespace:'a'},{namespace:'b'},{namespace:'a'}]}, 'namespace', '多空间 · a, b'],
    [{duration_ms:0}, 'duration', '0 ms'],
    [{duration_ms:'0'}, 'duration', '—'],
    [{tool:'search_meta',params:{keywords:['a','b','c'],keywords_total:20,keywords_truncated:true}}, 'summary', '+17'],
    [{tool:'search_meta',params:{keywords:['a'],keywords_truncated:true}}, 'summary', '日志已截断'],
    [{tool:'search_meta',params:{keywords:['a',{},'b','c','d']}}, 'summary', 'keywords: a, b, c · +1'],
    [{tool:'delete',status:'error',error:{type:'ValueError',message:'bad'},result:{success:true}}, 'outcome', 'ValueError · bad'],
  ];
  for (const [event, field, expected] of cases) {
    const before=JSON.stringify(event), actual=view(event);
    assert(actual[field].includes(expected), 'presentation ' + event.tool + ': ' + field + ' expected ' + expected + ', got ' + actual[field]);
    assert(JSON.stringify(event)===before, 'presentation preserves event');
  }
  assert(view({}).namespace==='—', 'missing namespace never called All');
  assert(view({status:'ok',result:{items:[]}}).status==='ok', 'status never inferred from result');
  assert(!view({tool:'model_config',result:{proxy:{url:'secret'}}}).summary.includes('secret'), 'config secrets not in summary');
  assert(!view({tool:'get_details',result:{found:[]}}).outcome.includes('缺失'), 'missing array never fabricated');
}
