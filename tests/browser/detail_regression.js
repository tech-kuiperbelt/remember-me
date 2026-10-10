async function runDetailScenario(scenario) {
  if (!scenario.startsWith('detail-')) return false;
  await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','initial list');
  const detail=await import('/admin/console/static/js/detail.js');
  const router=await import('/admin/console/static/js/router.js');
  const model=await import('/admin/console/static/js/detail-model.js');
  const markdown=await import('/admin/console/static/js/markdown.js');
  const store=await import('/admin/console/static/js/store.js');
  const namespaceOptions=()=>[...$('detail-namespace-options').querySelectorAll('[role=option]')];
  const chooseNamespace=value=>{if(!$('detail-namespace-options'))$('detail-namespace-select').click();namespaceOptions().find(option=>option.dataset.namespace===value).click();};
  const make=id=>({id,namespace:'a',summary:null,text:'# Detail heading\n\n**Bold** and [safe](https://example.com) [bad](javascript:alert(1))\n\n![remote](https://example.com/never-load.png)\n\n<img src=x onerror=alert(1)>\n\n```js\nMemoryService.update()\n```\n\n| Key | Value |\n| --- | --- |\n| name | test |',metadata:{categories:['design'],keywords:Array.from({length:24},(_,i)=>'keyword'+i),anchors:['MemoryService','handle_update_memory','get_details'],custom:{retain:true}},created_at:'2026-01-01T00:00:00Z',updated_at:'2026-02-01T00:00:00Z'});
  const records=new Map(['detail-a','detail-b'].map(id=>[id,make(id)]));
  let holdPut=false, releasePut=null;
  let getFailure=false,putFailure=false,deleteFailure=false,namespaceReject=false,held=null;
  S.setOverride((url,init)=>{
    if(url.startsWith('/api/memories/detail-')){
      const id=url.split('/').pop(),method=init?.method||'GET';
      if(method==='GET') { if(held) return new Promise(resolve=>{held(resolve);held=null;}); if(getFailure) return json({detail:'reread failed'},503); return records.has(id)?json(records.get(id)):json({detail:'missing'},404); }
      if(method==='PUT') { if(holdPut) return new Promise(resolve=>{releasePut=()=>{holdPut=false;const payload=JSON.parse(init.body),row=records.get(id);records.set(id,{...row,...payload,summary:payload.summary===''?null:payload.summary??row.summary,metadata:{...row.metadata,...payload.metadata}});resolve(json({success:true}));};}); if(putFailure)return json({detail:'write refused'},503); const payload=JSON.parse(init.body);if(namespaceReject&&!payload.confirm_codes)return json({detail:'unknown',response_code:'namespace_not_found',response_meta:{requested_namespace:payload.namespace}},400); const row=records.get(id); records.set(id,{...row,...payload,summary:payload.summary===''?null:payload.summary??row.summary,metadata:{...row.metadata,...payload.metadata}});return json({success:true}); }
      if(method==='DELETE'){if(deleteFailure)return json({detail:'delete refused'},503); records.delete(id);return json({success:true});}
    }
    return undefined;
  });
  const open=async id=>{detail.openMemory(id);await waitFor(()=>$('detail-edit'),'detail read');await waitFor(() => !$('detail-panel').getAnimations().length, 'drawer entrance finished');};
  const type=(id,value)=>{$(id).value=value;fire($(id),'input');};
  const escape=()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  const writes=()=>S.calls.filter(c=>c.method==='PUT'&&c.url.includes('/detail-'));
  const rowWidth=document.querySelector('.memory-row').getBoundingClientRect().width;
  if(scenario==='detail-functional') {
    $('btn-debug').click();
    const arrow=document.querySelector('.memory-row-detail-arrow'), rect=arrow.getBoundingClientRect();
    const target=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
    assert(target===arrow,'detail arrow is reachable in Debug');target.click();
    await waitFor(()=>$('detail-edit'),'open detail while Debug is active');
    assert(document.body.classList.contains('debug-ids')&&$('detail-debug').getAttribute('aria-pressed')==='true','opening detail retains Debug');
    assert($('detail-actions').contains($('detail-debug'))&&!$('detail-technical').open,'Debug visible with technical details collapsed');
    $('detail-technical-toggle').click();assert($('detail-technical').open,'technical details expand in Debug');
    $('detail-edit').click();assert(!$('edit-text'),'Debug still blocks editing');
    $('btn-detail-close').click();assert(!detail.isOpen()&&document.body.classList.contains('debug-ids'),'close detail retains Debug');
    $('btn-debug').click();
  }
  await open('detail-a');
  if(scenario==='detail-functional') {
    const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    detail.requestLeave();
    assert(!detail.isOpen() && $('detail-modal').inert, 'closing drawer cannot receive interaction');
    assert(reduced || $('detail-panel').getAnimations().length === 1, 'close plays a slide animation');
    await open('detail-b');
    assert($('detail-modal').style.display === 'flex' && !$('detail-modal').inert && $('detail-id-value').textContent === 'detail-b', 'reopening cancels stale close animation');
    detail.requestLeave();
    await waitFor(() => $('detail-modal').style.display === 'none', 'drawer hides after exit animation');
    await open('detail-a');
  }
  assert(document.querySelector('.memory-row').getBoundingClientRect().width===rowWidth,'background scrollbar and list width preserved');
  if(scenario!=='detail-functional') {
    if(scenario.includes('-edit')||scenario.includes('-preview')||scenario.includes('-confirm')){$('detail-edit').click();type('edit-text',records.get('detail-a').text+'\n\nUnsaved draft');}
    if(scenario.includes('-edit')) {
      $('detail-namespace-select').click();
      await settle();assert($('detail-namespace-options'),'namespace list remains open after focus settles');
      const popup=$('detail-namespace-options').getBoundingClientRect(), control=$('detail-namespace-control').getBoundingClientRect();
      assert(popup.left>=0&&popup.right<=innerWidth&&popup.top>=0&&popup.bottom<=innerHeight,'namespace popup remains inside viewport');
      assert(Math.abs(popup.right-control.right)<2,'namespace popup aligns with input right edge');
    }
    if(scenario.includes('-preview'))$('detail-preview-mode').click();
    if(scenario.includes('-confirm'))$('btn-detail-close').click();
    const expectedWidth=Math.min(innerWidth,Math.max(640,Math.min(innerWidth*0.6,1040)));
    assert(Math.abs($('detail-panel').getBoundingClientRect().width-expectedWidth)<1,'responsive drawer width');
    assert($('detail-panel').getBoundingClientRect().right<=innerWidth,'drawer right edge');
    assert($('app-shell').inert,'modal background inert');
    result.textContent='PASS: '+scenario;return true;
  }
  assert($('detail-summary-value').textContent==='—','null summary displayed faithfully');
  assert(!$('detail-score')&&!$('detail-content-markdown').querySelector('img,script,iframe,svg,[style],[onerror]'),'no resource-loading or unsafe Markdown');
  assert($('detail-content-markdown').querySelector('strong')&&$('detail-content-markdown').querySelector('.detail-code button')&&$('detail-content-markdown').querySelector('.detail-table'),'Markdown grammar and controlled actions');
  assert(![...$('detail-content-markdown').querySelectorAll('a')].some(a=>a.href.startsWith('javascript:')),'unsafe links removed');
  assert(!$('detail-keywords-expand')&&$('detail-keywords-24'),'all keywords visible without truncation');
  assert($('detail-categories-1').classList.contains('detail-tag-categories')&&$('detail-anchors-1').classList.contains('detail-tag-anchors'),'distinct metadata semantics');
  assert($('detail-created-value').title===records.get('detail-a').created_at&&/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test($('detail-created-value').textContent),'local time and raw ISO tooltip');
  assert($('detail-metadata').textContent===JSON.stringify(records.get('detail-a').metadata,null,2)&&$('detail-copy-metadata'),'highlighted JSON retains complete original data');
  $('detail-technical').open=true;
  assert($('detail-metadata').getBoundingClientRect().height<=240 && $('detail-metadata').scrollHeight>$('detail-metadata').clientHeight,'raw JSON caps at 240px and scrolls');
  const clipboardDescriptor=Object.getOwnPropertyDescriptor(navigator,'clipboard');const copied=[];
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>copied.push(value)}});
  $('detail-copy-id').click();await settle();$('detail-copy-metadata').click();await settle();
  assert(copied[0]==='detail-a'&&copied[1]===JSON.stringify(records.get('detail-a').metadata,null,2),'copies complete ID and raw JSON');
  assert($('detail-copy-id').textContent==='✓'&&$('detail-copy-metadata').textContent==='✓','copy has inline feedback');
  if(clipboardDescriptor)Object.defineProperty(navigator,'clipboard',clipboardDescriptor);else delete navigator.clipboard;
  $('detail-technical').open=false;
  const ids=[...$('detail-modal').querySelectorAll('[id]')].map(n=>n.id);assert(new Set(ids).size===ids.length,'unique region IDs');
  assert($('detail-actions').contains($('detail-debug'))&&!$('detail-technical').open,'drawer Debug is always in header');
  const box=$('detail-panel').getBoundingClientRect();$('detail-debug').click();assert($('detail-debug').getAttribute('aria-pressed')==='true','shared debug state');
  $('detail-technical-toggle').click();assert($('detail-technical').open,'Debug allows technical expansion');
  const codeCopy=$('detail-content-markdown-code-block-1-copy');
  assert(codeCopy&&[...$('detail-content-markdown').querySelectorAll('*')].every(el=>el.id),'Markdown elements and code copy have IDs');
  for(const id of ['detail-summary-value','detail-content-markdown-code-block-1-copy','detail-id-value']) {
    $(id).dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
    const label=document.querySelector('.debug-id-label');assert(!label.hidden&&label.textContent===id,'Debug shows '+id);
  }
  const copyLabel=codeCopy.textContent;codeCopy.click();assert(codeCopy.textContent===copyLabel&&codeCopy.classList.contains('debug-focus'),'Debug selects ID without copying code');
  escape();assert(document.activeElement===$('detail-debug')&&$('detail-panel').getBoundingClientRect().width===box.width,'debug exit and stable layout');
  $('detail-edit').click();assert(document.activeElement===$('edit-namespace')&&$('detail-save').disabled,'clean edit focuses Namespace: focus='+document.activeElement?.id+', disabled='+$('detail-save').disabled+', error='+$('detail-error').textContent);
  assert(!$('detail-extra-metadata-input'),'Edit has no raw JSON editor');
  const originalSummary=$('edit-summary').value, originalText=$('edit-text').value;
  type('edit-summary','line\n'.repeat(40));assert($('edit-summary').getBoundingClientRect().height<=130&&$('edit-summary').scrollHeight>$('edit-summary').clientHeight,'Summary caps and scrolls');
  type('edit-summary',originalSummary);assert($('edit-summary').getBoundingClientRect().height===64,'Summary shrinks to two lines');
  type('edit-text','line\n'.repeat(150));assert($('edit-text').getBoundingClientRect().height===500,'Content caps at 500px');
  $('detail-preview-mode').click();assert($('detail-preview').getBoundingClientRect().height===500,'Preview uses same height');
  $('detail-source-mode').click();type('edit-text',originalText);assert($('detail-save').disabled,'restoring source leaves clean draft');
  $('detail-picker-categories').click();type('detail-picker-categories-query','api');
  $('detail-picker-categories-query').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
  assert($('detail-picker-selected-categories').textContent.includes('api')&&!writes().length,'category Enter adds without saving');
  escape();$('detail-cancel').click();$('detail-edit').click();
  type('detail-anchor-input','');$('detail-anchor-add').click();assert(!$('detail-field-error-anchors').hidden,'anchor Add error is local');
  type('detail-anchor-input','MixedCaseAPI');$('detail-anchor-input').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
  assert($('detail-anchor-values').textContent.includes('MixedCaseAPI')&&$('detail-field-error-anchors').hidden,'anchor Enter preserves case and clears error');
  $('detail-cancel').click();$('detail-edit').click();
  const knownNamespaces=[...store.getNamespaces()];
  $('detail-namespace-select').click();
  store.setNamespaces([...knownNamespaces,'namespace-choice',...Array.from({length:30},(_,i)=>'namespace-long-candidate-'+i)]);
  assert(namespaceOptions().some(option=>option.dataset.namespace==='namespace-choice')&&$('edit-namespace').value==='a','live namespace candidates update without filtering current value');
  const popupBox=$('detail-namespace-options').getBoundingClientRect();
  assert(popupBox.left>=8&&popupBox.right<=innerWidth-8,'open namespace list is bounded by viewport');
  assert($('detail-namespace-options').scrollHeight>$('detail-namespace-options').clientHeight,'long namespace list scrolls inside bounded popup');
  escape();assert(!$('detail-namespace-options')&&document.activeElement===$('detail-namespace-select')&&detail.isOpen(),'Escape closes only namespace list');
  const dropdown=$('detail-namespace-select'), rect=dropdown.getBoundingClientRect();
  assert(document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2)===dropdown,'namespace dropdown is reachable');
  dropdown.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true}));
  $('detail-namespace-options').dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}));
  assert(document.activeElement===namespaceOptions().at(-1),'namespace list supports keyboard navigation');
  chooseNamespace('namespace-choice');
  assert($('edit-namespace').value==='namespace-choice'&&!$('detail-save').disabled&&document.activeElement===$('edit-namespace'),'dropdown selection updates draft and enables Save');
  $('detail-cancel').click();assert(records.get('detail-a').namespace==='a','cancel leaves namespace unchanged');
  store.setNamespaces(knownNamespaces);$('detail-edit').click();
  $('detail-debug').click();
  assert(!$('detail-namespace-select').dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true,cancelable:true})),'Debug blocks namespace dropdown');
  escape();
  const source=$('edit-text').value;textSelection();
  function textSelection(){ $('edit-text').setSelectionRange(3,12);$('edit-text').scrollTop=20; }
  assert($('detail-content-modes').getAttribute('role')==='tablist'&&$('detail-source-mode').getAttribute('aria-selected')==='true','content uses selected Edit tab');
  $('detail-source-mode').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));
  assert(document.activeElement===$('detail-preview-mode')&&$('detail-preview-mode').getAttribute('aria-selected')==='true'&&$('detail-source-panel').hidden&&!$('detail-preview').hidden,'Right arrow selects and focuses Preview');
  $('detail-preview-mode').dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true,cancelable:true}));
  assert(document.activeElement===$('detail-source-mode')&&$('detail-preview-mode').tabIndex===-1&&!$('detail-source-panel').hidden,'Home returns to Edit with one tab stop');
  assert($('edit-text').value===source&&$('edit-text').selectionStart===3&&$('edit-text').selectionEnd===12,'keyboard tabs preserve source and selection');
  $('detail-preview-mode').click();assert($('detail-preview').querySelector('h1'),'preview draft');$('detail-source-mode').click();assert($('edit-text').value===source&&$('edit-text').selectionStart===3&&$('edit-text').selectionEnd===12,'source and selection survive preview');
  type('edit-text',source+' changed');assert(!$('detail-save').disabled,'dirty enables save');
  $('btn-detail-close').click();assert($('detail-confirm-cancel')&&document.activeElement===$('detail-confirm-cancel'),'safe discard default');$('detail-confirm-cancel').click();assert($('edit-text').value.endsWith(' changed'),'keep draft');
  const composing=new KeyboardEvent('keydown',{key:'Escape',isComposing:true,bubbles:true,cancelable:true});document.dispatchEvent(composing);assert(!$('detail-confirm'),'IME Escape ignored');
  router.switchPage('keywords');assert($('detail-confirm'),'navigation guarded');router.switchPage('stats');$('detail-confirm-discard').click();assert($('page-keywords').classList.contains('active')&&!detail.isOpen(),'first pending navigation runs once');router.switchPage('memories');
  await open('detail-a');$('detail-edit').click();type('edit-text',source+' saved');holdPut=true;S.resetCalls();$('detail-save').click();$('detail-save')?.click();await waitFor(()=>releasePut,'held PUT');assert($('detail-save').disabled&&$('detail-save').textContent==='Saving…','Saving label and duplicate prevention');escape();$('detail-modal').click();$('btn-detail-close').click();assert(detail.isOpen()&&!$('detail-confirm'),'busy ignores all dismissal paths');releasePut();await waitFor(()=>$('detail-edit'),'save and GET');
  assert(writes().length===1,'one PUT');const payload=JSON.parse(writes()[0].body);assert(payload.summary===''&&JSON.stringify(payload.metadata.anchors)===JSON.stringify(records.get('detail-a').metadata.anchors),'body sends blank summary and original ordered anchors');assert($('detail-summary-value').textContent==='—'&&records.get('detail-a').metadata.custom.retain,'null stored and unknown metadata retained');
  $('detail-edit').click();type('edit-summary','new summary');getFailure=true;S.resetCalls();$('detail-save').click();await waitFor(()=>$('detail-retry'),'saved reload error');assert($('detail-status').textContent.includes('已保存')&&!$('edit-text'),'saved error is read only');getFailure=false;$('detail-retry').click();await waitFor(()=>$('detail-edit'),'retry GET');assert(writes().length===1&&$('detail-summary-value').textContent==='new summary','retry never repeats PUT');
  $('detail-edit').click();type('edit-summary','');putFailure=true;$('detail-save').click();await waitFor(()=>$('detail-error').textContent.includes('write refused'),'PUT error');assert($('edit-summary').value===''&&!$('detail-save').disabled,'failed write retains draft');putFailure=false;$('detail-cancel').click();assert($('detail-summary-value').textContent==='new summary','cancel returns confirmed view');
  $('detail-edit').click();type('edit-namespace','new-space');namespaceReject=true;const originalConfirm=window.confirm;window.confirm=()=>false;$('detail-save').click();await waitFor(()=>$('detail-save')&&!$('detail-save').disabled,'namespace confirmation cancel');assert($('edit-namespace').value==='new-space','namespace cancel retains draft');window.confirm=originalConfirm;namespaceReject=false;$('detail-cancel').click();
  $('detail-edit').click();
  type('edit-summary','  new summary  '); assert($('detail-save').disabled,'trim-equivalent summary stays clean');
  type('edit-namespace','not-yet-created'); $('detail-picker-keywords').click();
  S.resetCalls(); type('detail-picker-keywords-query','new-keyword'); await settle(350);
  assert(!S.calls.some(c=>c.url==='/api/keywords/query'),'unknown namespace skips scoped keyword request');
  $('detail-picker-keywords-query').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true})); assert(!$('detail-save').disabled,'explicit new keyword allowed above 20');
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
    await open(created.id); assert($('detail-summary-value').textContent==='—','real GET null summary');
    $('detail-edit').click();type('edit-text','MemoryService detail integration changed');S.resetCalls();$('detail-save').click();await waitFor(()=>$('detail-edit'),'real body save');
    let authoritative=await call('/memories/'+created.id);assert(authoritative.summary===null&&authoritative.metadata.anchors.join()==='MemoryService,handle_update_memory,get_details'&&authoritative.metadata.custom==='preserved','real storage preserves null and anchors');
    assert(S.calls.filter(c=>c.method==='PUT'&&c.url.endsWith(created.id)).length===1,'real UI one PUT');
    $('detail-edit').click();type('edit-summary','stored summary');$('detail-save').click();await waitFor(()=>$('detail-edit'),'real summary save');assert($('detail-summary-value').textContent==='stored summary','real summary persisted');
    $('detail-edit').click();type('edit-summary','');$('detail-save').click();await waitFor(()=>$('detail-edit'),'real clear summary');authoritative=await call('/memories/'+created.id);assert(authoritative.summary===null,'real explicit clear stored null');
    const stats=await call('/stats');store.setNamespaces(stats.namespaces);
    $('detail-edit').click();const targetNamespace=stats.default_namespace;
    $('detail-namespace-select').click();
    assert(namespaceOptions().some(option=>option.dataset.namespace===targetNamespace),'real configured default is selectable');
    chooseNamespace(targetNamespace);
    S.resetCalls();$('detail-save').click();await waitFor(()=>$('detail-edit'),'real namespace save');
    authoritative=await call('/memories/'+created.id);
    assert(authoritative.namespace===targetNamespace&&authoritative.text==='MemoryService detail integration changed'&&authoritative.summary===null&&authoritative.metadata.custom==='preserved','dropdown namespace persists without changing content or metadata');
    const namespaceWrites=S.calls.filter(c=>c.method==='PUT'&&c.url.endsWith(created.id));
    assert(namespaceWrites.length===1&&JSON.stringify(JSON.parse(namespaceWrites[0].body))===JSON.stringify({namespace:targetNamespace}),'namespace selection sends only namespace');
    $('detail-more').click();$('detail-delete').click();$('detail-confirm-delete').click();await waitFor(()=>!detail.isOpen(),'real delete');
    const gone=await S.realFetch('/api/memories/'+created.id);assert(gone.status===404,'real deletion confirmed');
  } finally {
    detail.requestLeave();await S.realFetch('/api/memories/'+created.id,{method:'DELETE'});
  }
  S.reset();result.textContent='PASS: Detail isolated contracts, safe Markdown, drafts, guards, race protection, save/read retry and deletion';return true;
}
