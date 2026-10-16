async function runCardsScenario(scenario) {
  await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'cards bootstrap');
  if ($('app-shell').classList.contains('sidebar-collapsed') !== (innerWidth <= 390)) $('nav').click();
  const rows=Array.from({length:6},(_,i)=>({id:'card-'+i,namespace:i===4 ? '' : 'remember-me',
    summary:i===0 ? '长摘要 Long summary '.repeat(20) : 'Card '+i,
    text:i===0 ? '完整内容 Content preview '.repeat(30) : i===1 ? 'Card 1' : 'Different body preview',
    metadata:{categories:['design','bugfix','verification'],keywords:Array.from({length:18},(_,j)=>'keyword-'+j),anchors:['MemoryService','EMBED_MODEL','SQLiteStorage']},
    updated_at:'2026-10-09T00:30:00+08:00',created_at:'2026-01-01T00:00:00Z'}));
  rows[1].metadata={}; rows[2].namespace='长空间'.repeat(20).slice(0,64);
  rows[3].metadata={keywords:['UnbrokenIdentifier'.repeat(30),'later'],anchors:['MixedCase']};
  rows[4].metadata={}; rows[4].updated_at='invalid';
  rows[5].metadata={categories:['design'],keywords:['cache'],anchors:['MixedCase']};
  for(let i=6;i<21;i++)rows.push({...rows[5],id:'card-'+i,summary:'Card '+i});
  S.setOverride((url,init)=>{
    if(url.startsWith('/api/memories/page?')) {
      const params=new URL(url,location.origin).searchParams;
      const start=Number(params.get('offset')||0);
      return json({items:rows.slice(start,start+Number(params.get('limit')||20)),total:rows.length});
    }
    if(url.startsWith('/api/memories/card-')) {
      const id=url.split('/').pop();
      if(init?.method==='DELETE'){rows.splice(rows.findIndex(row=>row.id===id),1);return json({success:true});}
      return json(rows.find(row=>row.id===id));
    }
    return undefined;
  });
  $('btn-refresh').click(); await waitFor(()=>$('memory-row-1')?.dataset.memoryId==='card-0','cards loaded');
  const visible=chip=>Boolean(chip.getClientRects().length);
  function verify() {
    for(const row of $('memories-list').querySelectorAll('.memory-row')) {
      const metadata=row.querySelector('.memory-row-metadata'), box=metadata.getBoundingClientRect();
      const chips=[...metadata.querySelectorAll('.memory-category-chip,.memory-keyword-chip,.memory-anchor-chip')];
      const hidden=chips.filter(chip=>!visible(chip)).length, more=row.querySelector('.memory-tags-more');
      assert(box.height<=54.5 && metadata.scrollWidth<=metadata.clientWidth+1,'two measured metadata rows without overflow');
      assert(more.hidden===!hidden && (!hidden || more.textContent==='+'+hidden), 'count includes exactly hidden labels');
      for(const chip of chips.filter(visible)) {
        const rect=chip.getBoundingClientRect();
        assert(rect.left>=box.left-1 && rect.right<=box.right+1 && rect.bottom<=box.bottom+1,'visible label is whole and in bounds');
      }
      if(!more.hidden) {
        const rect=more.getBoundingClientRect();
        assert(rect.right<=box.right+1 && rect.bottom<=box.bottom+1,'overflow button remains fully visible');
      }
      assert(row.querySelector('.memory-row-detail-arrow').tabIndex===-1,'one main row Tab entry');
      const arrow=row.querySelector('.memory-row-detail-arrow').getBoundingClientRect(), rowBox=row.getBoundingClientRect();
      assert(Math.abs(arrow.top+arrow.height/2-rowBox.top-rowBox.height/2)<1,'arrow vertically centered');
      assert(row.querySelector('.memory-row-title').getBoundingClientRect().height<=42.5,'summary at most two lines');
      const preview=row.querySelector('.memory-row-summary');
      assert(!preview || preview.getBoundingClientRect().height<=40.5,'content at most two lines');
    }
  }
  await settle(100); verify();
  assert(!$('memory-row-2-summary') && !$('memory-row-2-categories'),'empty preview and metadata do not reserve rows');
  assert(!$('memory-row-5-namespace'),'empty namespace group hidden');
  assert($('memory-row-3-namespace').title===rows[2].namespace,'long namespace remains available in tooltip');
  assert($('memory-row-3-namespace').getBoundingClientRect().width<=160.5,'long namespace stays single-line and capped');
  assert($('memory-row-4-tags-more').textContent==='+3','oversized first token hides ordered suffix accurately');
  const stamp=new Date(rows[0].updated_at);
  const local=[stamp.getFullYear(),String(stamp.getMonth()+1).padStart(2,'0'),String(stamp.getDate()).padStart(2,'0')].join('-');
  assert($('memory-row-1-date').textContent===local && /^\d{4}-\d{2}-\d{2}$/.test($('memory-row-5-date').textContent),'local date and invalid-update fallback');
  assert($('memory-row-6-anchor-1').textContent==='MixedCase','anchors preserve case');
  assert($('content').scrollWidth<=$('content').clientWidth+1,'no horizontal workspace overflow');
  if(scenario==='cards-functional') {
    const metadata=$('memory-row-1-metadata'), initial=Number(metadata.dataset.hiddenCount);
    const workspace=$('memories-list'); $('memory-row-1-tags-more').focus(); workspace.style.width='480px';
    await waitFor(()=>Number(metadata.dataset.hiddenCount)>initial,'resize recomputes hidden count'); verify();
    assert(document.activeElement===$('memory-row-1-tags-more'),'resize retains overflow focus');
    workspace.style.width=''; await waitFor(()=>Number(metadata.dataset.hiddenCount)===initial,'resize restores labels');
    document.fonts.dispatchEvent(new Event('loadingdone')); await settle(50); verify();
    const detail=await import('/admin/console/static/js/detail.js');
    const open=async trigger=>{
      S.resetCalls(); trigger.click(); await waitFor(()=>$('detail-edit'),'detail from card');
      assert(S.calls.filter(call=>call.url==='/api/memories/card-0').length===1,'one detail request per action');
      assert($('detail-keywords-values').querySelectorAll('.detail-tag-keywords').length>0,'hidden tags available in existing detail');
      $('btn-detail-close').click(); await waitFor(()=>$('detail-modal').style.display==='none','drawer exit');
      assert(!detail.isOpen(),'detail closes normally');
      assert($('memory-row-1').classList.contains('is-recent'),'closing detail retains recent marker');
      const marker=getComputedStyle($('memory-row-1'),'::before');
      assert(marker.width==='2px' && marker.height==='24px','recent marker is a short line');
      assert(getComputedStyle($('memory-row-1')).backgroundColor===getComputedStyle($('memory-row-2')).backgroundColor
        && getComputedStyle($('memory-row-1')).borderColor===getComputedStyle($('memory-row-2')).borderColor,'recent and focused row keep ordinary background and border');
    };
    await open($('memory-row-1-tags-more'));
    await open($('memory-row-1-open'));
    await open($('memory-row-1-detail-arrow'));
    $('btn-debug').click(); $('memory-row-1-tags-more').click(); assert(!detail.isOpen(),'Debug blocks overflow business action'); $('btn-debug').click();
    $('btn-refresh').click();await waitFor(()=>$('memory-row-1')?.classList.contains('is-recent'),'refresh retains marker by ID');
    $('memories-pagination').querySelector('[aria-label="' + t('memories.pagination.next') + '"]').click();
    await waitFor(()=>$('memory-row-1')?.dataset.memoryId==='card-20','next page');
    assert(!document.querySelector('.memory-row.is-recent'),'other page does not inherit position marker');
    $('memories-pagination').querySelector('[aria-label="' + t('memories.pagination.prev') + '"]').click();
    await waitFor(()=>$('memory-row-1')?.classList.contains('is-recent'),'return restores marker');
    detail.openMemory('card-0');await waitFor(()=>$('detail-edit'),'edit for navigation guard');
    $('detail-edit').click();$('edit-text').value+='\nunsaved';fire($('edit-text'),'input');
    detail.openMemory('card-5');assert($('detail-confirm-discard'),'guard shown');
    $('detail-confirm-cancel').click();
    assert($('memory-row-1').classList.contains('is-recent')&&!$('memory-row-6').classList.contains('is-recent'),'cancelled navigation retains marker');
    $('detail-cancel').click();$('btn-detail-close').click();
    await waitFor(()=>$('detail-modal').style.display==='none','guarded drawer closes');
    $('memory-row-6-open').click();await waitFor(()=>$('detail-edit'),'other detail');
    assert($('memory-row-6').classList.contains('is-recent')&&!$('memory-row-1').classList.contains('is-recent'),'opening another record moves marker');
    const deleted=rows.find(row=>row.id==='card-5');
    $('detail-more').click();$('detail-delete').click();$('detail-confirm-delete').click();
    await waitFor(()=>!detail.isOpen() && !$('memories-list').querySelector('[data-memory-id="card-5"]'),'delete refresh');
    rows.splice(5,0,deleted);$('btn-refresh').click();
    await waitFor(()=>$('memories-list').querySelector('[data-memory-id="card-5"]'),'restore test record');
    assert(!document.querySelector('.memory-row.is-recent'),'successful deletion clears remembered ID');
    $('memory-row-1-open').click();await waitFor(()=>$('detail-edit'),'final recent example');
    $('btn-detail-close').click();await waitFor(()=>$('detail-modal').style.display==='none','final example close');
    document.activeElement.blur();
    verify();
  } else {
    $('memory-row-1-open').click();await waitFor(()=>$('detail-edit'),'recent layout example');
    $('btn-detail-close').click();await waitFor(()=>$('detail-modal').style.display==='none','recent layout close');
    document.activeElement.blur();
  }
  result.textContent='PASS: '+scenario;
}
