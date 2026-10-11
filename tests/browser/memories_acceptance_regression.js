// Cross-feature acceptance. Mutations and failures stay at the isolated fetch boundary.
async function runMemoriesAcceptanceRegression() {
  const ready = () => waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'acceptance result');
  const searchCalls = () => S.calls.filter(c => c.url === '/api/memories/search');
  const query = value => { $('memories-query').value = value; fire($('memories-query'), 'input'); };
  const action = label => { if(label==='Delete') { $('detail-more').click(); $('detail-delete').click(); $('detail-confirm-delete').click(); } else $(label==='Save'?'detail-save':label==='Cancel'?'detail-cancel':label==='Close'?'btn-detail-close':'detail-edit').click(); };
  const normal = {state:'normal',action:null,required_reason:null,model:{status:'ready',ready:true}};
  let maintenance = normal, readError = null, writeError = false, invalid = null, pending = null;
  let rows = Array.from({length:21}, (_, i) => ({id:'accept-' + i, text:'Acceptance ' + i,
    summary:'Acceptance ' + i, namespace:'a', metadata:{categories:['design'],keywords:['cache']},
    score:1 - i / 100, created_at:'2026-01-01T00:00:00Z', updated_at:'2026-02-01T00:00:00Z'}));
  const records = new Map(rows.map(row=>[row.id,row]));
  S.reset();
  if ($('detail-modal').style.display === 'flex') {
    $('detail-cancel')?.click(); $('btn-detail-close').click();
  }
  if (!$('memories-search-clear').disabled) $('memories-search-clear').click();
  if (!$('memories-reset').hidden) $('memories-reset').click(); await ready();
  S.setOverride((url, init) => {
    if (url === '/api/maintenance') return maintenance ? json(maintenance) : Promise.reject(Error('status unavailable'));
    if (url === '/api/memories/search') {
      if (pending) return new Promise(resolve => { const receive = pending; pending = null; receive(resolve); });
      if (readError) return json({detail:readError,response_code:'maintenance_mode'},503);
      return json(invalid || {items:rows,filter_resolution:null});
    }
    if (url.startsWith('/api/memories/page?') && readError) return json({detail:readError},503);
    if (url.startsWith('/api/memories/accept-')) {
      const id = url.split('/').pop(), row = records.get(id);
      if (['PUT','DELETE'].includes(init?.method)) {
        if (writeError) return json({detail:'maintenance write denied',response_code:'maintenance_mode'},503);
        if(init.method==='PUT') records.set(id,{...row,...JSON.parse(init.body)}); else records.delete(id);
        rows = rows.filter(item => item.id !== id); return json({success:true});
      }
      return json(row);
    }
    return undefined;
  });
  try {
    $('memories-namespace').value='a'; fire($('memories-namespace'),'change'); await ready();
    $('memories-k').value='25'; query('acceptance'); submit($('memories-search-form')); await ready();
    $('memories-page-size').value='10'; fire($('memories-page-size'),'change');
    $('memories-pagination').querySelector('[aria-label="下一页"]').click();
    $('memories-pagination').querySelector('[aria-label="下一页"]').click();
    const last=$('memories-list').querySelector('.memory-open'); last.focus(); last.click();
    await waitFor(()=>$('detail-edit'),'last search detail');
    assert(document.activeElement===$('btn-detail-close'),'detail receives focus on opening');
    $('detail-edit').focus();
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));
    assert(document.activeElement===$('detail-technical-toggle'),'reverse Tab wraps within drawer: '+document.activeElement.id);
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));
    assert(document.activeElement===$('detail-edit'),'Tab wraps to first control');
    const confirm = window.confirm; window.confirm=()=>true;
    S.resetCalls(); try { action('Delete'); } finally { window.confirm=confirm; }
    await waitFor(()=>$('memories-count').textContent.includes('20 条') && $('memories-list').getAttribute('aria-busy')==='false','search deletion refresh').catch(e=>{throw Error(e.message+'; count='+$('memories-count').textContent+'; requests='+JSON.stringify(S.calls)+'; live='+rows.length);});
    assert(searchCalls().length===1 && $('memories-pagination').textContent.includes('11–20'),'search deletion corrects last page without second inference');
    assert(document.activeElement === $('memories-count'),'deleted search row restores result focus');
    assert($('memories-namespace').value==='a' && $('memories-query').value==='acceptance','deletion preserves scope and query');
    $('memories-list').querySelector('.memory-open').click();
    await waitFor(()=>$('detail-edit'),'edit detail'); action('Edit');
    await waitFor(()=>$('edit-text'),'edit form'); assert(document.activeElement===$('edit-namespace'),'editing receives namespace focus'); $('edit-text').value='no longer matches'; fire($('edit-text'),'input'); S.resetCalls(); action('Save');
    await waitFor(()=>$('memories-count').textContent.includes('19 条') && $('memories-list').getAttribute('aria-busy')==='false','edit removes search hit');
    assert(searchCalls().length===1 && !$('memories-list').querySelector('[data-memory-id="accept-10"]'),'edit refresh removes stale row');
    await waitFor(()=>$('detail-edit'),'saved view'); assert($('app-shell').inert,'saved drawer remains modal'); action('Close'); assert(document.activeElement === $('memories-count'),'edited-away row restores result focus');

    query('unsubmitted draft'); $('memories-k').value='30';
    $('nav-stats').click(); await settle(); S.resetCalls(); $('nav-memories').click(); await ready();
    assert(searchCalls().length===1 && JSON.parse(searchCalls()[0].body).query==='acceptance','return refreshes submitted query once');
    assert($('memories-query').value==='unsubmitted draft' && $('memories-k').value==='30','return preserves both drafts');
    let release; pending=resolve=>{release=resolve;}; $('btn-refresh').click();
    await waitFor(()=>release,'pending before leaving'); $('nav-stats').click(); $('nav-memories').click(); await ready();
    release(json({items:[],filter_resolution:null})); await settle();
    assert($('memories-count').textContent.includes('19 条'),'old response across navigation ignored');

    maintenance={...normal,state:'maintenance'}; writeError=true;
    await waitFor(()=>$('btn-status').textContent==='维护中','maintenance status');
    S.resetCalls(); $('btn-refresh').click(); await ready();
    assert(searchCalls().length===1 && $('memories-list').querySelector('.memory-row'),'maintenance search remains readable');
    $('memories-list').querySelector('.memory-open').click(); await waitFor(()=>$('detail-edit'),'maintenance detail');
    action('Edit'); await waitFor(()=>$('edit-text'),'maintenance edit'); $('edit-text').value+=' changed'; fire($('edit-text'),'input'); action('Save');
    await waitFor(()=>$('detail-error')?.textContent.includes('maintenance write denied'),'write refusal visible');
    assert($('detail-modal').style.display==='flex','write refusal retains edit form'); action('Cancel'); action('Close');
    $('memories-search-clear').click(); await ready();
    assert($('memories-list').querySelector('.memory-row'),'maintenance browse remains readable');
    query('acceptance'); submit($('memories-search-form')); await ready();
    for (const reason of ['model_not_ready','database switching']) {
      readError=reason; $('btn-refresh').click(); await ready();
      assert($('memories-list').textContent.includes(reason) && !$('memories-list').querySelector('.memory-row'),'read refusal is an error: '+reason);
      readError=null; [...$('memories-list').querySelectorAll('button')].find(b=>b.textContent==='Retry').click(); await ready();
      assert($('memories-list').querySelector('.memory-row'),'read retry restores search');
    }
    maintenance=null; await waitFor(()=>$('btn-status').textContent==='状态不可用','unavailable status');
    $('btn-refresh').click(); await ready(); assert($('memories-list').querySelector('.memory-row'),'status outage does not gate working search');
    for (const broken of [[], {items:[{...rows[0],score:'invalid'}],filter_resolution:null},
      {items:rows,filter_resolution:{keywords:'invalid'}}]) {
      invalid=broken; $('btn-refresh').click(); await ready();
      assert($('memories-count').textContent==='加载失败' && !$('memories-filter-resolution').textContent,'bad response fails safely');
    }
    invalid=null; maintenance=normal; writeError=false; $('btn-refresh').click(); await ready();
    // IME composition Enter is cancelled and never submits a partial query.
    S.resetCalls(); $('memories-query').dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
    $('memories-query').value='输入中'; fire($('memories-query'),'input');
    const key=new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true});
    $('memories-query').dispatchEvent(key);
    assert(key.defaultPrevented && !searchCalls().length,'IME Enter does not search');
    $('memories-query').dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));
    assert(!searchCalls().length,'composition end retains draft without submitting');

    // Stats keyword drilldown clears old search, filters and dates in one request.
    $('memories-updated-after').value='2020-01-01T00:00:00';
    $('nav-stats').click();
    await waitFor(()=>$('stats-keywords-ranking'),'Stats keyword ranking');
    if($('stats-namespace').value!=='a'){$('stats-namespace').value='a';fire($('stats-namespace'),'change');}
    await waitFor(()=>$('stats-content').getAttribute('aria-busy')==='false'&&[...$('stats-keywords-ranking').querySelectorAll('button')].some(b=>b.dataset.value==='cache'),'Stats keyword candidate');
    S.resetCalls(); [...$('stats-keywords-ranking').querySelectorAll('button')].find(b=>b.dataset.value==='cache').click(); await ready();
    const calls=S.calls.filter(c=>c.url.startsWith('/api/memories/page?'));
    assert(calls.length===1 && !searchCalls().length,'keyword jump replaces search once: '+JSON.stringify(S.calls));
    const params=new URL(calls[0].url,location.origin).searchParams;
    assert(params.get('namespace')==='a' && params.getAll('keywords').join()==='cache'
      && !params.has('categories') && !params.has('updated_after'),'keyword jump uses only target exact scope');
    assert(!$('memories-query').value && !$('memories-updated-after').value,'keyword jump clears drafts');
  } finally {
    S.reset(); $('memories-search-clear').click();
    if (!$('memories-reset').hidden) $('memories-reset').click();
    $('memories-k').value='10'; setAdvancedOpen(false);
    await ready();
  }
}
