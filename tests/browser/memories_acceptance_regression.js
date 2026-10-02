// Cross-feature acceptance. Mutations and failures stay at the isolated fetch boundary.
async function runMemoriesAcceptanceRegression() {
  const ready = () => waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'acceptance result');
  const searchCalls = () => S.calls.filter(c => c.url === '/api/memories/search');
  const query = value => { $('memories-query').value = value; fire($('memories-query'), 'input'); };
  const action = label => [...$('detail-actions').children].find(node => node.textContent === label).click();
  const normal = {state:'normal',action:null,required_reason:null,model:{status:'ready',ready:true}};
  let maintenance = normal, readError = null, writeError = false, invalid = null, pending = null;
  let rows = Array.from({length:21}, (_, i) => ({id:'accept-' + i, text:'Acceptance ' + i,
    summary:'Acceptance ' + i, namespace:'a', metadata:{categories:['design'],keywords:['cache']},
    score:1 - i / 100, created_at:'2026-01-01T00:00:00Z', updated_at:'2026-02-01T00:00:00Z'}));
  S.reset();
  if ($('detail-modal').style.display === 'flex') {
    [...$('detail-actions').children].find(node=>['Close','Cancel'].includes(node.textContent))?.click();
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
      const id = url.split('/').pop(), row = rows.find(item => item.id === id);
      if (['PUT','DELETE'].includes(init?.method)) {
        if (writeError) return json({detail:'maintenance write denied',response_code:'maintenance_mode'},503);
        rows = rows.filter(item => item.id !== id); return json({id});
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
    await waitFor(()=>$('detail-modal').style.display==='flex','last search detail');
    assert(document.activeElement===$('btn-detail-close'),'detail receives focus on opening');
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));
    assert(document.activeElement.textContent==='Close','detail reverse Tab wraps to last action');
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));
    assert(document.activeElement===$('btn-detail-close'),'detail Tab wraps to first action');
    const confirm = window.confirm; window.confirm=()=>true;
    S.resetCalls(); try { action('Delete'); } finally { window.confirm=confirm; }
    await waitFor(()=>$('memories-count').textContent.includes('20 条') && $('memories-list').getAttribute('aria-busy')==='false','search deletion refresh').catch(e=>{throw Error(e.message+'; count='+$('memories-count').textContent+'; requests='+JSON.stringify(S.calls)+'; live='+rows.length);});
    assert(searchCalls().length===1 && $('memories-pagination').textContent.includes('11–20'),'search deletion corrects last page without second inference');
    assert(document.activeElement === $('memories-count'),'deleted search row restores result focus');
    assert($('memories-namespace').value==='a' && $('memories-query').value==='acceptance','deletion preserves scope and query');
    $('memories-list').querySelector('.memory-open').click();
    await waitFor(()=>$('detail-modal').style.display==='flex','edit detail'); action('Edit');
    await waitFor(()=>$('edit-text'),'edit form'); assert(document.activeElement===$('edit-text'),'editing receives text focus'); $('edit-text').value='no longer matches'; S.resetCalls(); action('Save');
    await waitFor(()=>$('memories-count').textContent.includes('19 条') && $('memories-list').getAttribute('aria-busy')==='false','edit removes search hit');
    assert(searchCalls().length===1 && !$('memories-list').querySelector('[data-memory-id="accept-10"]'),'edit refresh removes stale row');
    assert(document.activeElement === $('memories-count'),'edited-away row restores result focus');

    query('unsubmitted draft'); $('memories-k').value='30';
    $('nav-keywords').click(); await settle(); S.resetCalls(); $('nav-memories').click(); await ready();
    assert(searchCalls().length===1 && JSON.parse(searchCalls()[0].body).query==='acceptance','return refreshes submitted query once');
    assert($('memories-query').value==='unsubmitted draft' && $('memories-k').value==='30','return preserves both drafts');
    let release; pending=resolve=>{release=resolve;}; $('btn-refresh').click();
    await waitFor(()=>release,'pending before leaving'); $('nav-keywords').click(); $('nav-memories').click(); await ready();
    release(json({items:[],filter_resolution:null})); await settle();
    assert($('memories-count').textContent.includes('19 条'),'old response across navigation ignored');

    maintenance={...normal,state:'maintenance'}; writeError=true;
    await waitFor(()=>$('btn-status').textContent==='维护中','maintenance status');
    S.resetCalls(); $('btn-refresh').click(); await ready();
    assert(searchCalls().length===1 && $('memories-list').querySelector('.memory-row'),'maintenance search remains readable');
    $('memories-list').querySelector('.memory-open').click(); await waitFor(()=>$('detail-modal').style.display==='flex','maintenance detail');
    action('Edit'); await waitFor(()=>$('edit-text'),'maintenance edit'); action('Save');
    await waitFor(()=>document.body.textContent.includes('maintenance write denied'),'write refusal visible');
    assert($('detail-modal').style.display==='flex','write refusal retains edit form'); action('Cancel');
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

    // Keyword jump clears old search, filters and date drafts in one list request.
    $('memories-updated-after').value='2020-01-01T00:00:00';
    $('nav-keywords').click(); $('kw-mode').value='literal'; $('kw-ns').value='a';
    $('kw-query').value='cache'; submit($('form-keywords'));
    await waitFor(()=>[...$('kw-results').querySelectorAll('button')].some(b=>b.textContent==='cache'),'keyword jump candidate');
    S.resetCalls(); [...$('kw-results').querySelectorAll('button')].find(b=>b.textContent==='cache').click(); await ready();
    const calls=S.calls.filter(c=>c.url.startsWith('/api/memories/page?'));
    assert(calls.length===1 && !searchCalls().length,'keyword jump replaces search once');
    const params=new URL(calls[0].url,location.origin).searchParams;
    assert(params.get('namespace')==='a' && params.getAll('keywords').join()==='cache'
      && !params.has('categories') && !params.has('updated_after'),'keyword jump uses only target exact scope');
    assert(!$('memories-query').value && !$('memories-updated-after').value,'keyword jump clears drafts');
  } finally {
    S.reset(); $('memories-search-clear').click();
    if (!$('memories-reset').hidden) $('memories-reset').click();
    $('memories-k').value='10'; $('memories-advanced').open=false;
    await ready();
  }
}
