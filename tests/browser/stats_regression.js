async function runStatsScenario(scenario) {
  if (!scenario.startsWith('stats-'))return false;
  await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false'&&$('memories-namespace').options.length>1,'initial global registry');
  const router=await import('/admin/console/static/js/router.js'), store=await import('/admin/console/static/js/store.js');
  const globalCategories=JSON.stringify(store.getCategories()); let globalNamespaces=JSON.stringify(store.getNamespaces());
  router.switchPage('stats');
  await waitFor(()=>$('stats-overview'),'real statistics endpoint');
  assert($('stats-overview').querySelector('[data-metric=memories] dd').textContent==='63','real isolated API total');
  assert($('stats-anchors').textContent.includes(t('stats.ranking.empty',{dimension:t('stats.dimension.anchors')})),'real isolated empty anchors');
  const special='部门 <img src=x onerror=window.statsInjected=1> '+ 'LongNamespace'.repeat(7);
  let largeNumbers=false;
  const fixture=scope=>{
    const multiplier=largeNumbers?10000:1;
    const total=(scope===null?200:scope==='a'?180:scope==='b'?20:0)*multiplier;
    const dimension=(key)=>({unique_count:total?(key==='categories'?3:120):0,covered_memories:Math.floor(total*.8),coverage_ratio:total ? .8 : 0,
      top:total?Array.from({length:key==='categories'?3:100},(_,i)=>({value:key==='categories'?['design','api','ops'][i]:key==='anchors'&&i===0?'MemoryService.MixedCase':key==='keywords'&&i===0?'<svg onload=window.statsInjected=1> '+ 'long-keyword-'.repeat(6):key+'-'+i,count:Math.min(total,(100-i)*multiplier)})):[]});
    return {scope:{namespace:scope},total_memories:total,namespace_options:['a','b','default',special],default_namespace:'default',
      namespace_distribution:scope===null?[{name:'a',count:180*multiplier},{name:special,count:20*multiplier}]:total?[{name:scope,count:total}]:[],
      dimensions:Object.fromEntries(['categories','keywords','anchors'].map(key=>[key,dimension(key)]))};
  };
  let held=null, fail=false;
  S.setOverride((url,init)=>{
    if(url==='/api/stats')return json({namespaces:['a','b',special],namespace_counts:{a:180,b:20},category_counts:JSON.parse(globalCategories),default_namespace:'default'});
    if(url.startsWith('/api/stats/metadata')){
      const scope=new URL(url,location.origin).searchParams.get('namespace');
      if(held){const callback=held;held=null;return new Promise(resolve=>callback(()=>resolve(json(fixture(scope)))));}
      if(fail)return json({detail:'<img src=x> request refused'},503);
      if(scope==='missing')return json({detail:'unknown namespace',response_code:'namespace_not_found',response_meta:{known_namespaces:['a','default']}},400);
      return json(fixture(scope));
    }
    if(url.startsWith('/api/memories/page?'))return json({items:[],total:0});
    if(url==='/api/memories/search/anchor')return json([]);
    return undefined;
  });
  $('btn-refresh').click();await waitFor(()=>$('stats-overview').querySelector('[data-metric=memories] dd').textContent==='200','fixture overview');
  globalNamespaces=JSON.stringify(store.getNamespaces());
  const select=value=>{$('stats-namespace').value=value;fire($('stats-namespace'),'change');};
  const ready=()=>waitFor(()=>$('stats-content').getAttribute('aria-busy')==='false'&&$('stats-overview'),'stats ready');
  const calls=()=>S.calls.filter(call=>call.url.startsWith('/api/stats/metadata'));
  assert(document.querySelectorAll('.stats-metric').length===5&&document.querySelectorAll('.stats-dimensions>.stats-panel').length===3,'exact five metrics and three peer panels');
  assert(document.querySelectorAll('.stats-namespace-row').length===2&&$('stats-distribution').querySelector('svg[role=img]'),'full clickable distribution with donut');
  assert(document.querySelectorAll('#stats-overview .stats-metric-icon svg').length===5,'five Overview identity icons');
  assert(new Set([...document.querySelectorAll('#stats-overview .stats-metric-icon')].map(e=>getComputedStyle(e).color)).size===5,'distinct metric colors');
  assert($('stats-distribution').querySelector('.stats-namespace-columns').textContent===t('stats.namespace')+t('stats.count')+t('stats.percentage')&&$('stats-distribution').querySelector('circle').getAttribute('stroke-width')==='44'&&$('stats-distribution').querySelectorAll('.stats-donut-label').length===2,'table headings and thicker labelled donut');
  for(const key of ['categories','keywords','anchors'])assert($('stats-'+key+'-unique-count')&&$('stats-'+key+'-coverage-percent')&&$('stats-'+key+'-coverage-count')&&$('stats-'+key+'-coverage-progress'),'dimension Debug targets');
  const donut=$('stats-distribution').querySelector('svg'),box=donut.viewBox.baseVal,circle=donut.querySelector('circle'),outer=circle.r.baseVal.value+Number(circle.getAttribute('stroke-width'))/2;
  assert(circle.cx.baseVal.value-outer>box.x&&circle.cy.baseVal.value-outer>box.y&&circle.cx.baseVal.value+outer<box.x+box.width&&circle.cy.baseVal.value+outer<box.y+box.height,'donut stroke has margin inside every viewport edge');
  for(const key of ['categories','keywords','anchors']){
    const bars=[...$('stats-'+key+'-ranking').querySelectorAll('.stats-rank-bar>span')];
    assert(new Set(bars.map(e=>getComputedStyle(e).backgroundColor)).size===bars.length,'distinct Top 5 colors for '+key);
    assert(bars.every((bar,index)=>getComputedStyle(bar).backgroundColor===getComputedStyle($('stats-keywords-ranking').querySelectorAll('.stats-rank-bar>span')[index]).backgroundColor),'shared ranking palette for '+key);
  }
  assert(!window.statsInjected&&!$('stats-content').querySelector('img'),'safe user labels');
  assert($('stats-keywords').textContent.includes('80.0%')&&$('stats-keywords').textContent.includes(t('stats.coverageCount',{covered:160,total:200})),'coverage has count and scoped ratio');
  assert($('stats-keywords-ranking').querySelector('.stats-percent').textContent==='50.0%'&&$('stats-keywords-ranking').querySelector('.stats-rank-bar span').style.width==='50%','rank bars use scoped memory total');
  assert(JSON.stringify(store.getNamespaces())===globalNamespaces&&JSON.stringify(store.getCategories())===globalCategories,'metadata does not pollute global candidates');
  if(scenario!=='stats-functional') {
    if(innerWidth<700&&!document.querySelector('.app-shell').classList.contains('sidebar-collapsed'))$('nav').click();
    if(scenario.includes('-single-')){select('a');await ready();}
    if(scenario.includes('-long-')){
      $('stats-keywords-top-100').click();$('stats-anchors-top-30').click();
      largeNumbers=true;$('btn-refresh').click();await ready();
    }
    const width=$('page-stats').getBoundingClientRect().width;
    const overviewColumns=width>=1200?5:width>=700?3:2;
    assert(getComputedStyle(document.querySelector('.stats-overview-grid')).gridTemplateColumns.split(' ').length===overviewColumns,'Overview reflows 5/3/2');
    [...document.querySelectorAll('.stats-metric')].forEach((metric,index)=>{
      assert((getComputedStyle(metric).borderLeftWidth==='0px')===(index%overviewColumns===0),'Overview separators only within a row');
    });
    const heading=$('stats-overview-title').getBoundingClientRect(), firstMetric=document.querySelector('.stats-metric-icon').getBoundingClientRect();
    assert(Math.abs(heading.left-firstMetric.left)<1,'Overview icon aligns with heading');
    assert(getComputedStyle(document.querySelector('.stats-panel h2')).fontSize==='16px'&&getComputedStyle(document.querySelector('.stats-metric dt')).fontSize==='13px'&&getComputedStyle(document.querySelector('.stats-rank-bar')).height==='8px','mockup typography and bar weight');
    assert(getComputedStyle(document.querySelector('.stats-rank-name')).fontSize==='13px'&&getComputedStyle(document.querySelector('.stats-rank-name')).lineHeight==='20px','readable ranking typography');
    assert([...document.querySelectorAll('.stats-rank-row,.stats-namespace-row')].every(row=>row.querySelector('svg[aria-hidden=true][focusable=false]')),'all drilldown rows show decorative arrows');
    assert(getComputedStyle(document.querySelector('.stats-dimensions')).gridTemplateColumns.split(' ').length===(width>=1200?3:width>=700?2:1),'3/2/1 columns follow container width');
    assert(document.documentElement.scrollWidth<=innerWidth,'no horizontal overflow');
    assert([...document.querySelectorAll('.stats-panel')].every(panel=>panel.getBoundingClientRect().right<=innerWidth),'panels fit container');
    assert([...document.querySelectorAll('.stats-rank-row,.stats-namespace-row')].every(row=>[...row.children].every(child=>child.getBoundingClientRect().right<=row.getBoundingClientRect().right+1)),'labels, counts and arrows stay inside rows');
    if($('stats-distribution')){const headers=$('stats-distribution').querySelector('.stats-namespace-columns'),row=$('stats-distribution').querySelector('.stats-namespace-row');
      assert(Math.abs(headers.querySelector('.stats-count').getBoundingClientRect().right-row.querySelector('.stats-count').getBoundingClientRect().right)<1&&Math.abs(headers.querySelector('.stats-percent').getBoundingClientRect().right-row.querySelector('.stats-percent').getBoundingClientRect().right)<1,'distribution headers align with values');
      assert([...headers.children].every(span=>span.scrollWidth<=span.clientWidth+2),'distribution headers do not overflow');}
    for(const card of document.querySelectorAll('.stats-dimensions>.stats-panel')){
      const controls=card.querySelector('.stats-top-controls').getBoundingClientRect(), metrics=card.querySelector('.stats-dimension-metrics').getBoundingClientRect();
      if(card.clientWidth-parseFloat(getComputedStyle(card).paddingLeft)*2>=360){assert(Math.abs(controls.top-metrics.top)<1,'Top controls align with dimension summary');
        const row=card.querySelector('.stats-rank-row');if(row)assert(getComputedStyle(row.querySelector('.stats-rank-bar')).gridRowStart==='1','wide ranking bars share the text row');}
      const unique=card.querySelector('.stats-dimension-metrics>div:first-child').getBoundingClientRect(),coverage=card.querySelector('.stats-dimension-metrics>div+div').getBoundingClientRect();
      assert(getComputedStyle(card.querySelector('.stats-dimension-metrics>div+div')).borderLeftWidth==='1px'&&Math.abs(coverage.left-unique.right-12)<1,'Unique and Coverage separator has compact spacing');
    }
    const before=$('stats-namespace').getBoundingClientRect().width;$('nav').click();await settle();
    const changed=$('page-stats').getBoundingClientRect().width;
    assert(getComputedStyle(document.querySelector('.stats-dimensions')).gridTemplateColumns.split(' ').length===(changed>=1200?3:changed>=700?2:1),'sidebar resize updates columns');
    $('nav').click();await settle();
    assert(Math.abs($('stats-namespace').getBoundingClientRect().width-before)<1,'sidebar restores geometry');
    if(scenario.includes('-long-'))assert($('stats-keywords-ranking').clientHeight<=440&&$('stats-keywords-ranking').scrollHeight>$('stats-keywords-ranking').clientHeight,'long rankings scroll internally');
    result.textContent='PASS: '+scenario;return true;
  }
  const theme=await import('/admin/console/static/js/theme.js');
  const originalTheme=document.documentElement.dataset.themePreference;
  const geometry=()=>[...document.querySelectorAll('#page-stats .stats-panel, #stats-namespace, .stats-top-controls')].map(node=>{
    const r=node.getBoundingClientRect();return [r.x,r.y,r.width,r.height];
  });
  const beforeTheme=JSON.stringify(geometry());
  theme.setTheme('dark');await settle();
  assert(JSON.stringify(geometry())===beforeTheme,'light/dark keep identical geometry');
  theme.setTheme(originalTheme);await settle();
  $('stats-keywords-top-30').focus();
  assert(document.activeElement.id==='stats-keywords-top-30','ranking controls accept keyboard focus');
  $('stats-keywords-top-30').click();
  assert(document.activeElement.id==='stats-keywords-top-30','ranking re-render restores control focus');
  $('stats-keywords-top-5').click();
  const totalCalls=calls().length;$('stats-keywords-top-100').click();$('stats-anchors-top-30').click();
  assert(calls().length===totalCalls&&$('stats-keywords-ranking').children.length===100&&$('stats-anchors-ranking').children.length===30&&$('stats-categories-top-5').getAttribute('aria-pressed')==='true','independent top switches without fetch');
  $('stats-keywords-ranking').scrollTop=200;$('stats-anchors-ranking').scrollTop=120;
  $('stats-categories-top-30').click();assert($('stats-keywords-ranking').scrollTop===200&&$('stats-anchors-ranking').scrollTop===120,'other panels keep scroll');
  $('stats-keywords-top-30').click();assert($('stats-keywords-ranking').scrollTop===0,'changed ranking starts at top');
  select('a');await ready();
  assert($('stats-namespace').value==='a'&&!$('stats-distribution')&&$('stats-overview').querySelector('[data-metric=namespaces] dd').textContent==='1','selector filters Stats and hides distribution');
  assert($('stats-namespace').options.length===5&&JSON.stringify(store.getNamespaces())===globalNamespaces,'selector stays global');
  for(const [key,field] of [['categories','categories'],['keywords','keywords'],['anchors','query']]) {
    $('stats-keywords-top-100').click();$('stats-keywords-ranking').scrollTop=180;$('content').scrollTop=80;
    const pageScroll=$('content').scrollTop, value=$('stats-'+key+'-ranking').querySelector('button').dataset.value;
    S.resetCalls();$('stats-'+key+'-ranking').querySelector('button').click();
    await waitFor(()=>$('page-memories').classList.contains('active')&&$('memories-list').getAttribute('aria-busy')==='false','drilldown '+key);
    const requests=S.calls.filter(call=>call.url.startsWith('/api/memories/page?')||call.url.startsWith('/api/memories/search'));
    assert(requests.length===1,'single drilldown request');
    if(key==='anchors') {
      const body=JSON.parse(requests[0].body);assert(requests[0].url==='/api/memories/search/anchor'&&body.query===value&&body.namespace==='a'&&body.k===10&&!('min_score' in body),'literal Anchor mode transfer');
      assert($('memories-search-mode').value==='anchor'&&$('memories-k').value==='10'&&$('memories-threshold').value==='','Anchor advanced drafts match submitted defaults');
    } else {
      const params=new URL(requests[0].url,location.origin).searchParams;
      assert(params.get(field)===value.trim().toLowerCase()&&params.get('namespace')==='a'&&!params.has(key==='keywords'?'categories':'keywords')&&!params.has('updated_after'),'exact '+key+' browse transfer');
    }
    router.switchPage('stats');await ready();
    assert($('stats-namespace').value==='a'&&$('stats-keywords-top-100').getAttribute('aria-pressed')==='true'&&$('stats-keywords-ranking').scrollTop===180&&$('content').scrollTop===pageScroll,'return restores Stats context');
  }
  select('');await ready();
  $('stats-keywords-ranking').scrollTop=160;$('content').scrollTop=80;
  const namespacePageScroll=$('content').scrollTop;
  S.resetCalls();
  [...document.querySelectorAll('.stats-namespace-row')].find(row=>row.dataset.namespace===special).click();
  await waitFor(()=>$('page-memories').classList.contains('active')&&$('memories-list').getAttribute('aria-busy')==='false','namespace drilldown');
  const namespaceRequests=S.calls.filter(call=>call.url.startsWith('/api/memories/page?')||call.url.startsWith('/api/memories/search'));
  assert(namespaceRequests.length===1&&namespaceRequests[0].url.startsWith('/api/memories/page?'),'namespace issues one Browse request');
  const namespaceParams=new URL(namespaceRequests[0].url,location.origin).searchParams;
  assert(namespaceParams.get('namespace')===special&&namespaceParams.get('offset')==='0'&&!['categories','keywords','updated_after','updated_before'].some(key=>namespaceParams.has(key))&&$('memories-query').value==='','namespace clears previous Anchor query and other filters');
  assert(calls().length===0,'namespace row does not request scoped Stats');
  router.switchPage('stats');await ready();
  assert($('stats-namespace').value===''&&$('stats-distribution')&&$('stats-keywords-top-100').getAttribute('aria-pressed')==='true'&&$('stats-keywords-ranking').scrollTop===160&&$('content').scrollTop===namespacePageScroll,'namespace return restores All Stats context');
  select('default');await ready();
  assert([...$('stats-overview').querySelectorAll('dd')].every(node=>node.textContent==='0')&&!$('stats-distribution')&&$('stats-keywords').textContent.includes('0.0%'),'empty legal default scope');
  let release;held=callback=>{release=callback;};select('a');await waitFor(()=>release,'held scope');select('b');await ready();release();await settle(50);
  assert($('stats-namespace').value==='b'&&$('stats-overview').querySelector('[data-metric=memories] dd').textContent==='20','stale response cannot overwrite scope');
  fail=true;$('btn-refresh').click();await waitFor(()=>$('stats-content').querySelector('[role=alert]'),'explicit failure');
  assert(!$('stats-content').querySelector('img'),'error is safe text');fail=false;
  [...$('stats-content').querySelectorAll('button')].find(node=>node.textContent===t('detail.action.retry')).click();await ready();
  $('stats-namespace').append(new Option('missing','missing'));select('missing');await waitFor(()=>$('stats-content').querySelector('[role=alert]'),'unknown namespace');
  [...$('stats-content').querySelectorAll('button')].find(node=>node.textContent===t('stats.namespace.all')).click();await ready();assert($('stats-distribution'),'return All after error');
  held=callback=>{release=callback;};$('btn-refresh').click();await waitFor(()=>held===null,'held refresh');router.switchPage('memories');release();await settle(50);assert(!$('page-stats').classList.contains('active'),'hidden response cannot navigate');
  // A rejected router leave guard does not run the drilldown preparation.
  const detail=await import('/admin/console/static/js/detail.js');
  detail.openCreate();await waitFor(()=>$('edit-text'),'create drawer');$('edit-text').value='unsaved';fire($('edit-text'),'input');
  const previousQuery=$('memories-query').value;router.switchPage('stats');assert($('detail-confirm')&&$('page-memories').classList.contains('active'),'draft guard protects navigation');
  $('detail-confirm-cancel').click();assert($('memories-query').value===previousQuery,'cancelled navigation keeps state');
  $('btn-detail-close').click();$('detail-confirm-discard').click();
  result.textContent='PASS: '+scenario;return true;
}
