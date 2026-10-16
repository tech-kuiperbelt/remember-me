async function runCreateScenario(scenario) {
  if(!scenario.startsWith('create-'))return false;
  await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','create entry');
  const detail=await import('/admin/console/static/js/detail.js');
  const store=await import('/admin/console/static/js/store.js');
  const router=await import('/admin/console/static/js/router.js');
  S.freezeMaintenance();store.setMaintenance({state:'normal'});
  const assertMetadataLayout=()=>{
    const sections=['categories','keywords','anchors'].map(key=>$('detail-edit-'+key));
    assert(!$('detail-edit-tags')&&sections.every(Boolean),'three separate metadata fields');
    assert(sections.map(section=>section.querySelector('h2').textContent).join(',')===[t('detail.field.categories'),t('detail.field.keywords'),t('detail.field.anchors')].join(','),'peer metadata headings');
    const boxes=sections.map(section=>section.getBoundingClientRect());
    assert(boxes.every(box=>Math.abs(box.left-boxes[0].left)<1),'metadata fields align left');
    assert(boxes.slice(1).every((box,i)=>Math.abs(box.top-boxes[i].bottom-24)<1),'metadata fields stack with 24px spacing');
    for(const key of ['categories','keywords'])assert($('detail-edit-'+key).contains($('detail-picker-'+key))&&$('detail-edit-'+key).contains($('detail-field-error-'+key)),'picker and error belong to '+key);
  };
  const type=(id,value)=>{$(id).value=value;fire($(id),'input');};
  const escape=()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  const open=()=>{$('memories-create').focus();$('memories-create').click();};
  const discard=()=>{$('btn-detail-close').click();if($('detail-confirm-discard'))$('detail-confirm-discard').click();};
  const posts=()=>S.calls.filter(c=>c.url==='/api/memories'&&c.method==='POST');
  let memoryEvents=0,statsEvents=0;
  store.onMemoriesChanged(()=>memoryEvents++);store.onStatsChanged(()=>statsEvents++);
  assert(!$('page-add')&&!$('nav-add'),'independent Add page removed');
  store.setDefaultNamespace('ready','configured-project');open();
  assertMetadataLayout();
  assert($('detail-title').textContent===t('detail.title.new')&&$('edit-namespace').value==='configured-project','configured default used');
  assert(!$('detail-technical')&&$('detail-debug')&&$('detail-save').disabled,'create identity hidden and empty body blocked');
  assert(!$('detail-extra-metadata-input')&&!$('detail-extra-metadata'),'Create has no raw JSON editor');
  assert($('detail-error').hidden && !$('detail-field-error-text').textContent,'Create initially has no red validation');
  assert($('detail-summary-hint').textContent.includes(t('detail.summary.hint')),'automatic summary explained');
  assert(!$('detail-default-namespace-retry').getClientRects().length,'loaded default hides retry button visually');
  if(scenario==='create-functional') {
    $('detail-namespace-select').click();
    const option=[...$('detail-namespace-options').querySelectorAll('[role=option]')].find(option=>option.dataset.namespace==='a');
    assert(option,'create lists existing namespaces despite filled default');option.click();
    assert($('edit-namespace').value==='a','create namespace dropdown updates draft');
    type('edit-namespace','configured-project');
  }
  if(scenario!=='create-functional') {
    type('edit-text','# New memory\n\nDraft with **Markdown**.');
    if(scenario.includes('-preview-'))$('detail-preview-mode').click();
    if(scenario.includes('-confirm-'))$('btn-detail-close').click();
    const ids=[...$('detail-modal').querySelectorAll('[id]')].map(el=>el.id);
    assert(new Set(ids).size===ids.length,'create IDs unique');
    assert(document.documentElement.scrollWidth<=innerWidth,'create fits viewport');
    const expected=Math.min(innerWidth,Math.max(640,Math.min(innerWidth*.6,1040)));
    assert(Math.abs($('detail-panel').getBoundingClientRect().width-expected)<1,'create responsive width');
    result.textContent='PASS: '+scenario;return true;
  }
  $('detail-cancel').click();assert(!detail.isOpen()&&document.activeElement===$('memories-create'),'clean cancel and entry focus');
  store.setDefaultNamespace('loading');open();assert($('edit-namespace').value===''&&$('detail-save').disabled,'no guessed default');
  type('edit-namespace','manual-space');store.setDefaultNamespace('ready','late-default');assert($('edit-namespace').value==='manual-space','late default preserves input');
  discard();store.setDefaultNamespace('error',null,Error('offline'));open();
  assert(!$('detail-default-namespace-retry').hidden,'default retry visible');
  store.setDefaultNamespace('ready','configured-project');assert($('edit-namespace').value==='configured-project','unmodified blank takes loaded default');
  $('detail-debug').click();$('edit-text').dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
  assert(document.querySelector('.debug-id-label').textContent==='edit-text','create Debug labels');
  escape();assert(document.activeElement===$('detail-debug'),'create Debug exit focus');
  type('edit-text','  ');fire($('edit-text'),'blur');assert($('detail-field-error-text').textContent===t('detail.error.textRequired')&&$('edit-text').getAttribute('aria-invalid')==='true','blur shows local Content error');assert($('detail-save').disabled,'whitespace body blocked');
  type('edit-text','# draft');assert(!$('detail-field-error-text').textContent,'correcting Content clears error');assert(!$('detail-save').disabled,'valid dedicated fields accepted');
  $('detail-preview-mode').click();assert($('detail-preview').querySelector('h1'),'create preview');$('detail-source-mode').click();assert($('edit-text').value==='# draft','preview retains source');
  $('detail-cancel').click();assert($('detail-confirm'),'dirty cancel guarded');$('detail-confirm-cancel').click();assert($('edit-text').value==='# draft','keep draft');
  const unload=new Event('beforeunload',{cancelable:true});window.dispatchEvent(unload);assert(unload.defaultPrevented,'create unload guard');
  let failure=true, duplicate=false, getFailure=false, namespaceFailure=false;
  const record={id:'created-test',namespace:'configured-project',text:'# draft',summary:'Generated',metadata:{source:'browser'},created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z'};
  S.setOverride((url,init)=>{
    if(url==='/api/memories'&&init?.method==='POST') {
      if(failure)return json({detail:'maintenance refused'},503);
      const body=JSON.parse(init.body);
      if(namespaceFailure&&!body.confirm_codes)return json({detail:'unknown',response_code:'namespace_not_found',response_meta:{requested_namespace:body.namespace}},400);
      Object.assign(record,{text:body.text,namespace:body.namespace,metadata:body.metadata,summary:body.summary||'Generated'});
      return json({id:record.id,created:!duplicate},201);
    }
    if(url==='/api/memories/created-test')return getFailure?json({detail:'GET failed'},503):json(record);
    return undefined;
  });
  S.resetCalls();$('detail-save').click();await waitFor(()=>!$('detail-save').disabled,'create failed');
  assert(posts().length===1&&$('edit-text').value==='# draft'&&$('detail-error').textContent.includes('maintenance refused'),'create failure retains draft');
  store.setMaintenance({state:'normal'});assert($('detail-error').textContent.includes('maintenance refused'),'maintenance poll preserves request error');
  store.setMaintenance({state:'maintenance'});assert($('detail-save').disabled&&$('edit-text').value==='# draft','maintenance locks create without clearing');store.setMaintenance({state:'normal'});
  failure=false;namespaceFailure=true;const confirm=window.confirm;window.confirm=()=>false;
  $('detail-save').click();await waitFor(()=>!$('detail-save').disabled,'namespace cancel');assert($('edit-text').value==='# draft','namespace cancel retains');
  window.confirm=()=>true;duplicate=true;S.resetCalls();$('detail-save').click();await waitFor(()=>$('detail-duplicate'),'duplicate notice');
  assert(posts().length===2&&JSON.parse(posts()[1].body).confirm_codes[0]==='namespace_not_found','namespace confirmed retry');window.confirm=confirm;namespaceFailure=false;
  assert($('detail-copy-draft')&&$('edit-text').value==='# draft','duplicate preserves draft');
  $('detail-view-existing').click();assert($('detail-confirm'),'opening duplicate guards draft');$('detail-confirm-cancel').click();
  duplicate=false;getFailure=true;S.resetCalls();$('detail-save').click();$('detail-save').click();await waitFor(()=>$('detail-retry'),'created GET error');
  assert(posts().length===1&&$('detail-status').textContent.includes(t('detail.status.createdLoadFailed',{error:''}).trim())&&!$('edit-text'),'created error clears editor');
  assert(memoryEvents===1&&statsEvents===1,'one change notification after created, none after duplicate');
  const payload=JSON.parse(posts()[0].body);assert(!Object.hasOwn(payload,'summary')&&Object.keys(payload.metadata).length===0,'summary omitted and no raw metadata editor payload');
  getFailure=false;$('detail-retry').click();await waitFor(()=>$('detail-edit'),'created GET retry');assert(posts().length===1&&$('detail-id-value').textContent===record.id,'GET retry does not POST');
  $('btn-detail-close').click();assert(document.activeElement===$('memories-create'),'created close returns entry');
  // Exact applied Namespace overrides configured default; other filters stay out.
  $('memories-namespace').value='a';fire($('memories-namespace'),'change');await settle();open();assert($('edit-namespace').value==='a'&&!$('detail-anchor-1'),'exact namespace only inherited');discard();
  router.switchPage('add');assert($('page-memories').classList.contains('active')&&!detail.isOpen(),'legacy route falls back without create');
  // Real POST -> GET -> duplicate -> DELETE on the isolated Admin/SQLite service.
  S.reset();S.freezeMaintenance();store.setMaintenance({state:'normal'});store.setDefaultNamespace('ready','default');detail.openCreate('a',{onClose:()=>$('memories-create').focus()});
  type('edit-text','Create browser integration unique body');
  $('detail-anchor-input').value='CreateAnchor';$('detail-anchor-add').click();S.resetCalls();$('detail-save').click();await waitFor(()=>$('detail-edit'),'real create');
  const id=$('detail-id-value').textContent;
  try {
    const response=await S.realFetch('/api/memories/'+id);const saved=await response.json();
    assert(response.ok&&saved.namespace==='a'&&saved.summary&&!Object.hasOwn(saved.metadata,'source')&&saved.metadata.anchors[0]==='CreateAnchor','real created fields and auto summary');
    assert(posts().length===1,'real single POST');$('btn-detail-close').click();
    detail.openCreate('a');type('edit-text',saved.text);type('edit-summary','Do not overwrite');$('detail-save').click();await waitFor(()=>$('detail-duplicate'),'real duplicate');
    const after=await (await S.realFetch('/api/memories/'+id)).json();assert(after.summary===saved.summary,'real duplicate untouched');discard();
  } finally {await S.realFetch('/api/memories/'+id,{method:'DELETE'});}
  result.textContent='PASS: create functional, default/loading, metadata, Debug, guards, POST/GET errors, duplicate and real SQLite';return true;
}
