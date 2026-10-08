async function runDetailScenario(scenario) {
  if (!scenario.startsWith('detail-')) return false;
  await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','initial list');
  const detail=await import('/admin/console/static/js/detail.js');
  const router=await import('/admin/console/static/js/router.js');
  const model=await import('/admin/console/static/js/detail-model.js');
  const markdown=await import('/admin/console/static/js/markdown.js');
  const make=id=>({id,namespace:'a',summary:null,text:'# Detail heading\n\n**Bold** and [safe](https://example.com) [bad](javascript:alert(1))\n\n![remote](https://example.com/never-load.png)\n\n<img src=x onerror=alert(1)>\n\n```js\nMemoryService.update()\n```\n\n| Key | Value |\n| --- | --- |\n| name | test |',metadata:{categories:['design'],keywords:Array.from({length:24},(_,i)=>'keyword'+i),anchors:['MemoryService','handle_update_memory','get_details'],custom:{retain:true}},created_at:'2026-01-01T00:00:00Z',updated_at:'2026-02-01T00:00:00Z'});
  const records=new Map(['detail-a','detail-b'].map(id=>[id,make(id)]));
  let getFailure=false,putFailure=false,deleteFailure=false,namespaceReject=false,held=null;
  S.setOverride((url,init)=>{
    if(url.startsWith('/api/memories/detail-')){
      const id=url.split('/').pop(),method=init?.method||'GET';
      if(method==='GET') { if(held) return new Promise(resolve=>{held(resolve);held=null;}); if(getFailure) return json({detail:'reread failed'},503); return records.has(id)?json(records.get(id)):json({detail:'missing'},404); }
      if(method==='PUT') { if(putFailure)return json({detail:'write refused'},503); const payload=JSON.parse(init.body);if(namespaceReject&&!payload.confirm_codes)return json({detail:'unknown',response_code:'namespace_not_found',response_meta:{requested_namespace:payload.namespace}},400); const row=records.get(id); records.set(id,{...row,...payload,summary:payload.summary===''?null:payload.summary??row.summary,metadata:{...row.metadata,...payload.metadata}});return json({success:true}); }
      if(method==='DELETE'){if(deleteFailure)return json({detail:'delete refused'},503); records.delete(id);return json({success:true});}
    }
    return undefined;
  });
  const open=async id=>{detail.openMemory(id);await waitFor(()=>$('detail-edit'),'detail read');};
  const type=(id,value)=>{$(id).value=value;fire($(id),'input');};
  const escape=()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  const writes=()=>S.calls.filter(c=>c.method==='PUT'&&c.url.includes('/detail-'));
  const rowWidth=document.querySelector('.memory-row').getBoundingClientRect().width;
  await open('detail-a');
  assert(document.querySelector('.memory-row').getBoundingClientRect().width===rowWidth,'background scrollbar and list width preserved');
  if(scenario!=='detail-functional') {
    if(scenario.includes('-edit')||scenario.includes('-preview')||scenario.includes('-confirm')){$('detail-edit').click();type('edit-text',records.get('detail-a').text+'\n\nUnsaved draft');}
    if(scenario.includes('-preview'))$('detail-preview-mode').click();
    if(scenario.includes('-confirm'))$('btn-detail-close').click();
    const expectedWidth=Math.min(innerWidth,Math.max(640,Math.min(innerWidth*0.6,1040)));
    assert(Math.abs($('detail-panel').getBoundingClientRect().width-expectedWidth)<1,'responsive drawer width');
    assert($('detail-panel').getBoundingClientRect().right<=innerWidth,'drawer right edge');
    assert($('app-shell').inert,'modal background inert');
    result.textContent='PASS: '+scenario;return true;
  }
  assert($('detail-summary-value').textContent==='无摘要','null summary displayed faithfully');
  assert(!$('detail-score')&&!$('detail-content-markdown').querySelector('img,script,iframe,svg,[style],[onerror]'),'no resource-loading or unsafe Markdown');
  assert($('detail-content-markdown').querySelector('strong')&&$('detail-content-markdown').querySelector('.detail-code button')&&$('detail-content-markdown').querySelector('.detail-table'),'Markdown grammar and controlled actions');
  assert(![...$('detail-content-markdown').querySelectorAll('a')].some(a=>a.href.startsWith('javascript:')),'unsafe links removed');
  assert($('detail-keywords-expand').textContent==='+19','keyword preview count');$('detail-keywords-expand').click();assert($('detail-keywords-24'),'all keywords expanded');
  const ids=[...$('detail-modal').querySelectorAll('[id]')].map(n=>n.id);assert(new Set(ids).size===ids.length,'unique region IDs');
  $('detail-technical').open=true;const box=$('detail-panel').getBoundingClientRect();$('detail-debug').click();assert($('detail-debug').getAttribute('aria-pressed')==='true','shared debug state');
  const codeCopy=$('detail-content-markdown-code-block-1-copy');
  assert(codeCopy&&[...$('detail-content-markdown').querySelectorAll('*')].every(el=>el.id),'Markdown elements and code copy have IDs');
  for(const id of ['detail-summary-value','detail-content-markdown-code-block-1-copy','detail-id-value']) {
    $(id).dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
    const label=document.querySelector('.debug-id-label');assert(!label.hidden&&label.textContent===id,'Debug shows '+id);
  }
  const copyLabel=codeCopy.textContent;codeCopy.click();assert(codeCopy.textContent===copyLabel&&codeCopy.classList.contains('debug-focus'),'Debug selects ID without copying code');
  escape();assert(document.activeElement===$('detail-debug')&&$('detail-panel').getBoundingClientRect().width===box.width,'debug exit and stable layout');
  $('detail-edit').click();assert(document.activeElement===$('edit-namespace')&&$('detail-save').disabled,'clean edit focuses Namespace');
  const source=$('edit-text').value;textSelection();
  function textSelection(){ $('edit-text').setSelectionRange(3,12);$('edit-text').scrollTop=20; }
  $('detail-preview-mode').click();assert($('detail-preview').querySelector('h1'),'preview draft');$('detail-source-mode').click();assert($('edit-text').value===source&&$('edit-text').selectionStart===3&&$('edit-text').selectionEnd===12,'source and selection survive preview');
  type('edit-text',source+' changed');assert(!$('detail-save').disabled,'dirty enables save');
  $('btn-detail-close').click();assert($('detail-confirm-cancel')&&document.activeElement===$('detail-confirm-cancel'),'safe discard default');$('detail-confirm-cancel').click();assert($('edit-text').value.endsWith(' changed'),'keep draft');
  const composing=new KeyboardEvent('keydown',{key:'Escape',isComposing:true,bubbles:true,cancelable:true});document.dispatchEvent(composing);assert(!$('detail-confirm'),'IME Escape ignored');
  router.switchPage('keywords');assert($('detail-confirm'),'navigation guarded');router.switchPage('stats');$('detail-confirm-discard').click();assert($('page-keywords').classList.contains('active')&&!detail.isOpen(),'first pending navigation runs once');router.switchPage('memories');
  await open('detail-a');$('detail-edit').click();type('edit-text',source+' saved');S.resetCalls();$('detail-save').click();$('detail-save')?.click();await waitFor(()=>$('detail-edit'),'save and GET');
  assert(writes().length===1,'one PUT');const payload=JSON.parse(writes()[0].body);assert(payload.summary===''&&JSON.stringify(payload.metadata.anchors)===JSON.stringify(records.get('detail-a').metadata.anchors),'body sends blank summary and original ordered anchors');assert($('detail-summary-value').textContent==='无摘要'&&records.get('detail-a').metadata.custom.retain,'null stored and unknown metadata retained');
  $('detail-edit').click();type('edit-summary','new summary');getFailure=true;S.resetCalls();$('detail-save').click();await waitFor(()=>$('detail-retry'),'saved reload error');assert($('detail-status').textContent.includes('已保存')&&!$('edit-text'),'saved error is read only');getFailure=false;$('detail-retry').click();await waitFor(()=>$('detail-edit'),'retry GET');assert(writes().length===1&&$('detail-summary-value').textContent==='new summary','retry never repeats PUT');
  $('detail-edit').click();type('edit-summary','');putFailure=true;$('detail-save').click();await waitFor(()=>$('detail-error').textContent.includes('write refused'),'PUT error');assert($('edit-summary').value===''&&!$('detail-save').disabled,'failed write retains draft');putFailure=false;$('detail-cancel').click();assert($('detail-summary-value').textContent==='new summary','cancel returns confirmed view');
  $('detail-edit').click();type('edit-namespace','new-space');namespaceReject=true;const originalConfirm=window.confirm;window.confirm=()=>false;$('detail-save').click();await waitFor(()=>$('detail-save')&&!$('detail-save').disabled,'namespace confirmation cancel');assert($('edit-namespace').value==='new-space','namespace cancel retains draft');window.confirm=originalConfirm;namespaceReject=false;$('detail-cancel').click();
  $('detail-edit').click();
  type('edit-summary','  new summary  '); assert($('detail-save').disabled,'trim-equivalent summary stays clean');
  type('edit-namespace','not-yet-created'); $('detail-picker-keywords').click();
  S.resetCalls(); type('detail-picker-keywords-query','new-keyword'); await settle(350);
  assert(!S.calls.some(c=>c.url==='/api/keywords/query'),'unknown namespace skips scoped keyword request');
  $('detail-picker-keywords-create').click(); assert(!$('detail-save').disabled,'explicit new keyword allowed above 20');
  escape(); assert(!$('detail-picker-keywords-popover')&&detail.isOpen(),'Escape closes picker first');
  const leaving=new Event('beforeunload',{cancelable:true});window.dispatchEvent(leaving);assert(leaving.defaultPrevented,'dirty registers unload guard');
  $('detail-cancel').click();const cleanUnload=new Event('beforeunload',{cancelable:true});window.dispatchEvent(cleanUnload);assert(!cleanUnload.defaultPrevented,'cancel removes unload guard');
  $('detail-edit').click();$('detail-picker-categories').click();
  const candidates=[...$('detail-picker-categories-popover').querySelectorAll('input[type=checkbox]')].filter(el=>!el.checked);
  candidates[0].click();const next=[...$('detail-picker-categories-popover').querySelectorAll('input[type=checkbox]')].find(el=>!el.checked&&!el.disabled);next.click();
  assert([...$('detail-picker-categories-popover').querySelectorAll('input[type=checkbox]')].filter(el=>!el.checked).every(el=>el.disabled),'category write limit three');
  escape();$('detail-cancel').click();
  $('detail-more').click();escape();assert(!$('detail-menu')&&detail.isOpen(),'Escape closes menu first');
  $('detail-more').click();$('detail-delete').click();assert($('detail-confirm-description').textContent.includes('detail-a'),'delete full ID');deleteFailure=true;$('detail-confirm-delete').click();await waitFor(()=>$('detail-confirm-error').textContent.includes('delete refused'),'delete error');assert(records.has('detail-a'),'no optimistic delete');deleteFailure=false;$('detail-confirm-delete').click();await waitFor(()=>!detail.isOpen(),'delete success');
  let release;held=resolve=>{release=resolve;};detail.openMemory('detail-a');await waitFor(()=>release,'delayed read');$('btn-detail-close').click();release(json(make('detail-a')));await settle();assert(!detail.isOpen(),'late response cannot reopen');
  held=resolve=>{release=resolve;};detail.openMemory('detail-a');await waitFor(()=>release,'A read');await open('detail-b');release(json(make('detail-a')));await settle();assert($('detail-id-value').textContent==='detail-b','A/B response guard');$('btn-detail-close').click();
  const historical=make('history');historical.metadata.categories=['a','b','c','d'];historical.metadata.keywords=['',123];const draft=model.createDraft(historical);assert(!model.isDirty(historical,draft),'historical values initially clean');draft.summary='change';assert(!model.validateDraft(draft,historical).categories,'unmodified historical dimension not cleaned');draft.keywords.pop();assert(model.validateDraft(draft,historical).keywords,'explicit historical repair required');
  const clean=make('clean'),d=model.createDraft(clean);d.anchors=['ＡＰＩ\u0085name','api name','😀'.repeat(128)];d.text+=' changed';assert(!model.validateDraft(d,clean).anchors&&model.buildUpdatePayload(clean,d).metadata.anchors.length===2,'NFKC, Python whitespace, ASCII dedupe and codepoints');d.anchors[2]+='😀';assert(model.validateDraft(d,clean).anchors,'129 codepoints rejected');
  const host=document.createElement('div');markdown.renderMarkdown(host,'[relative](/x) [protocol](//example.com) [mail](mailto:test@example.com)\n\n<script>alert(1)<\/script>');assert(host.querySelectorAll('a').length===1&&!host.querySelector('script'),'strict URL and escaped raw HTML');
  const purify=(await import('/admin/console/static/vendor/purify.js')).default;const sanitize=purify.sanitize;purify.sanitize=()=>{throw Error('failure');};try{markdown.renderMarkdown(host,'faithful source');assert(host.textContent.includes('渲染失败')&&host.textContent.includes('faithful source'),'render failure preserves source');}finally{purify.sanitize=sanitize;}
  // Real Admin HTTP + SQLite round trip in this runner's isolated service.
  S.reset();
  const call=async (path,method='GET',payload)=>{const response=await S.realFetch('/api'+path,{method,headers:{'Content-Type':'application/json'},...(payload?{body:JSON.stringify(payload)}:{})});assert(response.ok,'real Admin response '+response.status);return response.json();};
  const created=await call('/memories','POST',{namespace:'a',text:'MemoryService detail integration',summary:'initial summary',metadata:{categories:['design'],keywords:['cache'],anchors:['MemoryService','handle_update_memory','get_details'],custom:'preserved'}});
  try {
    await call('/memories/'+created.id,'PUT',{summary:''});
    await open(created.id); assert($('detail-summary-value').textContent==='无摘要','real GET null summary');
    $('detail-edit').click();type('edit-text','MemoryService detail integration changed');S.resetCalls();$('detail-save').click();await waitFor(()=>$('detail-edit'),'real body save');
    let authoritative=await call('/memories/'+created.id);assert(authoritative.summary===null&&authoritative.metadata.anchors.join()==='MemoryService,handle_update_memory,get_details'&&authoritative.metadata.custom==='preserved','real storage preserves null and anchors');
    assert(S.calls.filter(c=>c.method==='PUT'&&c.url.endsWith(created.id)).length===1,'real UI one PUT');
    $('detail-edit').click();type('edit-summary','stored summary');$('detail-save').click();await waitFor(()=>$('detail-edit'),'real summary save');assert($('detail-summary-value').textContent==='stored summary','real summary persisted');
    $('detail-edit').click();type('edit-summary','');$('detail-save').click();await waitFor(()=>$('detail-edit'),'real clear summary');authoritative=await call('/memories/'+created.id);assert(authoritative.summary===null,'real explicit clear stored null');
    $('detail-more').click();$('detail-delete').click();$('detail-confirm-delete').click();await waitFor(()=>!detail.isOpen(),'real delete');
    const gone=await S.realFetch('/api/memories/'+created.id);assert(gone.status===404,'real deletion confirmed');
  } finally {
    detail.requestLeave();await S.realFetch('/api/memories/'+created.id,{method:'DELETE'});
  }
  S.reset();result.textContent='PASS: Detail isolated contracts, safe Markdown, drafts, guards, race protection, save/read retry and deletion';return true;
}
