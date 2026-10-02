// Deterministic stress content for the desktop acceptance matrix.
async function runMemoriesVisualScenario(scenario) {
  if (!scenario.startsWith('acceptance-')) return false;
  await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','visual bootstrap');
  const long='long-scope-and-keyword-'.repeat(3).slice(0,64);
  const words=Array.from({length:20},(_,i)=>'keyword-'+i+'-'+long);
  const rows=Array.from({length:21},(_,i)=>({id:'visual-'+i,namespace:long,
    summary:'长标题 Long memory summary '+long.repeat(3),text:'Different preview 正文预览 '+long.repeat(8),
    metadata:{categories:['design','architecture','api'],keywords:words},score:.91,
    updated_at:'2026-01-01T00:00:00.000200+00:00'}));
  const mapping={keywords:words.map(input=>({input,match_type:'semantic',matches:[{value:input+'-resolved',accepted:true}]}))};
  S.setOverride(url=>{
    if(url.startsWith('/api/memories/page?')) return json({items:rows.slice(0,20),total:21});
    if(url==='/api/memories/search') return json({items:rows.slice(0,10),filter_resolution:mapping});
    if(url.startsWith('/api/memories/search/')) return json(rows.slice(0,10).map(row=>({...row,score:1e-9})));
    return undefined;
  });
  const collapsed=scenario.includes('-collapsed-');
  if($('app-shell').classList.contains('sidebar-collapsed')!==collapsed) $('nav').click();
  $('btn-refresh').click(); await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','visual browse');
  if(['-search','-lexical','-anchor'].some(mode=>scenario.includes(mode))) {
    if(scenario.endsWith('-lexical') || scenario.endsWith('-anchor')) {
      $('memories-mode-'+scenario.split('-').at(-1)).click(); $('memories-advanced').open=true;
    }
    $('memories-query').value='搜索输入 '+long.repeat(5); fire($('memories-query'),'input'); submit($('memories-search-form'));
    await waitFor(()=>$('memories-list').querySelector('.memory-search-score'),'visual search');
  } else $('memories-advanced').open=true;
  if(scenario.endsWith('-categories')) {
    $('memories-categories').click(); await waitFor(()=>document.querySelector('.memory-category-option'),'visual categories');
  }
  if(scenario.endsWith('-keywords')) {
    $('memories-keywords').click(); $('memories-keywords-query').value='word'; fire($('memories-keywords-query'),'input');
    await waitFor(()=>document.querySelector('[data-keyword]'),'visual keywords');
    for(let i=0;i<20;i++) document.querySelector('[data-keyword]:not(:disabled)').click();
    await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','visual selected keywords');
  }
  assert(innerWidth>=1024,'acceptance CSS width at least 1024');
  assert(document.documentElement.scrollWidth<=innerWidth,'no document horizontal overflow');
  assert($('content').scrollWidth<=$('content').clientWidth+1,'no workspace horizontal overflow');
  for(const id of ['memories-query','memories-search-mode','memories-search-submit','memories-search-clear','memories-sort','memories-advanced-apply']) {
    const node=$(id),box=node.getBoundingClientRect();
    if(!box.width)continue;
    assert(box.left>=0 && box.right<=innerWidth,'control fits horizontally: '+id);
  }
  const searchBoxes=['memories-query','memories-search-submit','memories-search-clear'].map(id=>$(id).getBoundingClientRect());
  assert(Math.max(...searchBoxes.map(b=>b.top+b.height/2))-Math.min(...searchBoxes.map(b=>b.top+b.height/2))<3,'search controls stay on one line');
  const modeBox=$('memories-search-mode').getBoundingClientRect(), filterBox=$('memories-filters').getBoundingClientRect(), rowBox=$('memories-filter-row').getBoundingClientRect();
  assert(Math.abs(modeBox.top-filterBox.top)<2 && Math.abs(modeBox.right-rowBox.right)<2,'mode aligns right on filter row');
  assert(modeBox.top>=Math.max(...searchBoxes.map(b=>b.bottom)),'mode is below search form');
  const popup=document.querySelector('.memory-filter-popover');
  if(popup) {
    const box=popup.getBoundingClientRect();
    assert(box.left>=0 && box.right<=innerWidth && box.top>=0 && box.bottom<=innerHeight,'popover fits viewport');
    assert(popup.scrollWidth<=popup.clientWidth+1,'popover content has no horizontal overflow');
  }
  result.textContent='PASS: desktop acceptance '+scenario;
  return true;
}
