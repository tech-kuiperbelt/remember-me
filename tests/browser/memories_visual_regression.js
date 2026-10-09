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
      $('memories-search-mode').value=scenario.split('-').at(-1); fire($('memories-search-mode'),'change'); $('memories-advanced').open=true;
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
  assert(innerWidth>=390,'acceptance CSS width at least 390');
  assert(document.documentElement.scrollWidth<=innerWidth,'no document horizontal overflow');
  assert($('content').scrollWidth<=$('content').clientWidth+1,'no workspace horizontal overflow');
  for (const row of $('memories-list').querySelectorAll('.memory-row')) {
    const footer = row.querySelector('.memory-row-footer').getBoundingClientRect();
    const date = row.querySelector('.memory-row-time').getBoundingClientRect();
    for (const group of row.querySelectorAll('.memory-tag-group')) {
      const box = group.getBoundingClientRect();
      assert(box.left >= footer.left - 1 && box.right <= date.left - 11, 'tag group stays inside metadata area');
      for (const chip of group.querySelectorAll('.memory-chip')) {
        const rect = chip.getBoundingClientRect(), style = getComputedStyle(chip);
        assert(rect.left >= box.left - 1 && rect.right <= box.right + 1
          && rect.top >= box.top - 1 && rect.bottom <= box.bottom + 1, 'chip stays inside its group');
        if (!chip.classList.contains('memory-tags-more')) {
          assert(rect.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) >= 31, 'chip retains readable text width');
        }
      }
    }
  }
  for(const id of ['memories-query','memories-search-mode','memories-search-submit','memories-search-clear','memories-sort','memories-advanced-apply']) {
    const node=$(id),box=node.getBoundingClientRect();
    if(!box.width)continue;
    assert(box.left>=0 && box.right<=innerWidth,'control fits horizontally: '+id);
  }
  const inputBox=$('memories-search-input').getBoundingClientRect(), queryBox=$('memories-query').getBoundingClientRect();
  const modeBox=$('memories-search-mode').getBoundingClientRect();
  assert(Math.abs(inputBox.height-44)<1 && Math.abs(modeBox.width-112)<1,'integrated search dimensions');
  assert(modeBox.left>=inputBox.left && modeBox.right<=queryBox.left+1 && Math.abs(modeBox.top-queryBox.top)<2,'mode precedes query inside search field');
  assert(!$('memories-filter-row').contains($('memories-search-mode')) && !$('memories-mode-semantic'),'old mode buttons removed from filters');
  const actionBoxes=['memories-search-submit','memories-search-clear'].map(id=>$(id).getBoundingClientRect());
  assert(Math.abs(actionBoxes[0].top-actionBoxes[1].top)<2,'search action buttons share a row');
  if(innerWidth<=768) assert(actionBoxes[0].top>=inputBox.bottom,'narrow actions wrap below integrated field');
  else assert(Math.abs(actionBoxes[0].top+actionBoxes[0].height/2-(inputBox.top+inputBox.height/2))<3,'desktop search stays on one line');
  const popup=document.querySelector('.memory-filter-popover');
  if(popup) {
    const box=popup.getBoundingClientRect();
    assert(box.left>=0 && box.right<=innerWidth && box.top>=0 && box.bottom<=innerHeight,'popover fits viewport');
    assert(popup.scrollWidth<=popup.clientWidth+1,'popover content has no horizontal overflow');
  }
  assert($('memory-row-1-anchors').querySelectorAll('.memory-anchor-chip').length === 3
    && $('memory-row-1-anchor-more').textContent === '+2', 'anchors display three with remaining count');
  assert($('memory-row-1-anchor-1').textContent === 'MemoryService', 'anchors preserve case and order');
  assert(!$('memory-row-2-anchors'), 'absent anchors omit the group');
  $('memory-row-1-anchor-more').click();
  const anchorPopover=document.querySelector('.memory-tags-popover');
  assert(anchorPopover?.querySelectorAll('.memory-chip').length === 5
    && anchorPopover.textContent.includes('BGE-M3') && !anchorPopover.textContent.includes('keyword:'), 'anchor overflow shows all anchors only');
  assert($('detail-modal').style.display !== 'flex', 'anchor overflow does not open detail');
  const anchorBox=anchorPopover.getBoundingClientRect();
  assert(anchorBox.left>=0 && anchorBox.right<=innerWidth && anchorBox.top>=0 && anchorBox.bottom<=innerHeight, 'anchor popover fits viewport');
  document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
  assert(!document.querySelector('.memory-tags-popover') && document.activeElement === $('memory-row-1-anchor-more'), 'anchor popover closes and restores focus');
  result.textContent='PASS: desktop acceptance '+scenario;
  return true;
}
