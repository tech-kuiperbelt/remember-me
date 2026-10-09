// Deterministic stress content for the desktop acceptance matrix.
async function runMemoriesVisualScenario(scenario) {
  if (!scenario.startsWith('acceptance-')) return false;
  await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','visual bootstrap');
  const long='long-scope-and-keyword-'.repeat(3).slice(0,64);
  const words=Array.from({length:20},(_,i)=>'keyword-'+i+'-'+long);
  const rows=Array.from({length:21},(_,i)=>({id:'visual-'+i,namespace:long,
    summary:'长标题 Long memory summary '+long.repeat(3),text:'Different preview 正文预览 '+long.repeat(8),
    metadata:{categories:['design','architecture','api'],keywords:words,
      anchors:i === 0 ? ['MemoryService', 'EMBED_MODEL', 'SQLiteStorage', 'NamespaceNotFound', 'BGE-M3'] : []},score:.91,
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
      $('memories-search-mode').value=scenario.split('-').at(-1); fire($('memories-search-mode'),'change'); setAdvancedOpen(true);
    }
    $('memories-query').value='搜索输入 '+long.repeat(5); fire($('memories-query'),'input'); submit($('memories-search-form'));
    await waitFor(()=>$('memories-list').querySelector('.memory-search-score'),'visual search');
  } else setAdvancedOpen(true);
  if(scenario.endsWith('-categories')) {
    $('memories-categories').click(); await waitFor(()=>document.querySelector('.memory-category-option'),'visual categories');
  }
  if(scenario.endsWith('-keywords')) {
    $('memories-keywords').click(); $('memories-keywords-query').value='word'; fire($('memories-keywords-query'),'input');
    await waitFor(()=>document.querySelector('[data-keyword]'),'visual keywords');
    for(let i=0;i<20;i++) document.querySelector('[data-keyword]:not(:disabled)').click();
    await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','visual selected keywords');
  }
  assert(innerWidth>=390,'acceptance CSS width at least 390');
  assert(document.documentElement.scrollWidth<=innerWidth,'no document horizontal overflow');
  assert($('content').scrollWidth<=$('content').clientWidth+1,'no workspace horizontal overflow');
  await settle(50);
  for (const row of $('memories-list').querySelectorAll('.memory-row')) {
    const metadata=row.querySelector('.memory-row-metadata'), box=metadata.getBoundingClientRect();
    const date=row.querySelector('.memory-row-time').getBoundingClientRect();
    assert(box.height<=54.5 && box.right<=date.left-11, 'metadata stays in two rows beside date');
    const chips=[...metadata.querySelectorAll('.memory-category-chip,.memory-keyword-chip,.memory-anchor-chip')];
    const visible=chips.filter(chip=>chip.getClientRects().length);
    const more=metadata.querySelector('.memory-tags-more');
    const hidden=chips.length-visible.length;
    assert(more.hidden === !hidden && (!hidden || more.textContent==='+'+hidden), 'unified overflow count matches actual visible tags');
    for(const chip of visible) {
      const rect=chip.getBoundingClientRect();
      assert(rect.left>=box.left-1 && rect.right<=box.right+1 && rect.bottom<=box.bottom+1, 'whole visible chip fits metadata');
    }
    assert(row.querySelector('.memory-row-detail-arrow').tabIndex===-1, 'arrow avoids duplicate Tab stop');
  }
  for(const id of ['memories-query','memories-search-mode','memories-search-submit','memories-search-clear','memories-sort','memories-advanced-apply']) {
    const node=$(id),box=node.getBoundingClientRect();
    if(!box.width)continue;
    assert(box.left>=0 && box.right<=innerWidth,'control fits horizontally: '+id);
  }
  const inputBox=$('memories-search-input').getBoundingClientRect(), queryBox=$('memories-query').getBoundingClientRect();
  const modeBox=$('memories-search-mode').getBoundingClientRect();
  assert(Math.abs(inputBox.height-40)<1 && Math.abs(modeBox.width-112)<1,'integrated search dimensions');
  assert(modeBox.left>=inputBox.left && modeBox.right<=queryBox.left+1 && Math.abs(modeBox.top-queryBox.top)<2,'mode precedes query inside search field');
  assert(!$('memories-filter-row').contains($('memories-search-mode')) && !$('memories-mode-semantic'),'old mode buttons removed from filters');
  const actionBoxes=['memories-search-submit','memories-search-clear'].map(id=>$(id).getBoundingClientRect());
  assert(Math.abs(actionBoxes[0].top-actionBoxes[1].top)<2,'search action buttons share a row');
  if(document.querySelector('.memories-workspace').clientWidth<640) assert(actionBoxes[0].top>=inputBox.bottom,'narrow actions wrap below integrated field');
  else assert(Math.abs(actionBoxes[0].top+actionBoxes[0].height/2-(inputBox.top+inputBox.height/2))<3,'desktop search stays on one line');
  const popup=document.querySelector('.memory-filter-popover');
  if(popup) {
    const box=popup.getBoundingClientRect();
    assert(box.left>=0 && box.right<=innerWidth && box.top>=0 && box.bottom<=innerHeight,'popover fits viewport');
    assert(popup.scrollWidth<=popup.clientWidth+1,'popover content has no horizontal overflow');
  }
  assert($('memory-row-1-anchor-1').textContent === 'MemoryService', 'anchors preserve case and order');
  assert(!$('memory-row-2-anchors'), 'absent anchors omit the group');
  result.textContent='PASS: desktop acceptance '+scenario;
  return true;
}
