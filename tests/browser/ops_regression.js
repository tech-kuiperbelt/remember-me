async function runOpsRegression(scenario) {
  await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','bootstrap');
  const ops=await import('/admin/console/static/js/ops.js');
  const normal={state:'normal',action:null,error:null,required_reason:null,model:{status:'ready',ready:true}};
  const ready={...normal,state:'maintenance'};
  let snapshot={...normal}, config={port:1314,language:(document.documentElement.lang||'zh-CN'),local_only:true,debug:true,debug_effective:true,auto_download_model:true,proxy:{enabled:false,url:'http://***@proxy:8080'}};
  let down=false, saveError=false, retryError=false, holdSync=null, holdSave=null, holdMaintenance=null, jobReadFail=false, importPosts=0, recoverPosts=0, clearScope=null;
  const writes=[], saved=[];
  S.setOverride((url,init)=>{
    if(url==='/api/maintenance'&&holdMaintenance)return new Promise(resolve=>{holdMaintenance=()=>resolve(json(snapshot));});
    if(url==='/api/maintenance')return down?Promise.reject(new Error('offline')):json(snapshot);
    if(url==='/api/config') {
      if(init?.method==='POST') {
        const value=JSON.parse(init.body);saved.push(value);
        if(saveError)return json({detail:'disk error'},400);
        config={...config,...value,debug_effective:value.debug,proxy:{...config.proxy,...value.proxy}};
        if(holdSave)return new Promise(resolve=>{holdSave=()=>resolve(json(config));});
      }
      return json(config);
    }
    if(url==='/api/model/retry')return retryError?json({detail:'retry refused'},409):json({status:'waiting'});
    if(url==='/api/sync'){writes.push(url);return holdSync?new Promise(resolve=>{holdSync=()=>resolve(json({synced:4,errors:0}));}):json({synced:4,errors:0});}
    if(url==='/api/import'){importPosts++;return json({import_id:'import-1'});}
    if(url==='/api/recover'){recoverPosts++;return json({id:'recover-1'});}
    if(url==='/api/import/import-1'||url==='/api/recover/recover-1')return jobReadFail?Promise.reject(new Error('poll failed')):json({status:'completed',result:{imported:2,skipped:1,renamed:1,recovered:4}});
    if(url.startsWith('/api/memories')&&init?.method==='DELETE'){clearScope=url;return json({deleted:3});}
    if(url==='/api/namespaces/migrate'){writes.push(JSON.parse(init.body));return json({migrated:2});}
    if(url.startsWith('/api/export/'))return new Response('{}\n',{headers:{'Content-Disposition':'attachment; filename="export-test.jsonl"','X-Memory-Count':'4'}});
    if(url==='/api/restart'||url==='/api/shutdown'){writes.push(url);snapshot={...normal};if(scenario==='ops-restart-timeout')down=true;return json({status:'accepted'});}
    return undefined;
  });
  $('nav-ops').click();await waitFor(()=>!$('ops-config-fields').disabled,'config loaded');
  if(innerWidth<=390&&!scenario.includes('sidebar')&&!$('app-shell').classList.contains('sidebar-collapsed'))$('nav').click();
  const input=(id,value)=>{const el=$(id);if(el.type==='checkbox')el.checked=value;else el.value=value;fire(el,'input');fire(el,'change');};
  const file=id=>{const dt=new DataTransfer();dt.items.add(new File(['{}\n'],'backup.jsonl'));$(id).files=dt.files;fire($(id),'change');};
  const setState=async value=>{snapshot=value;await ops.refreshMaintenance();};
  const code=async()=>{await waitFor(()=>$('clear-modal').style.display==='flex','confirmation');input('clear-confirm-input',$('clear-confirm-code').textContent);$('btn-clear-confirm').click();};
  if(scenario.includes('confirm-layout')) {
    $('ops-tab-maintenance').click();await setState(ready);$('ops-recover').open=true;file('recover-file');$('btn-recover').click();
    await waitFor(()=>$('clear-modal').style.display==='flex','responsive confirmation');
    const box=$('clear-modal').querySelector('.modal-panel').getBoundingClientRect();
    assert(box.left>=0&&box.right<=innerWidth&&box.top>=0&&box.bottom<=innerHeight,'confirmation fits viewport');
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));
    assert(document.activeElement===$('btn-clear-cancel'),'confirmation traps backward Tab');
    result.textContent='PASS: '+scenario;return;
  }
  if(scenario.includes('layout')||scenario.includes('expanded')) {
    if(scenario.includes('maintenance')){$('ops-tab-maintenance').click();await setState(ready);}
    if(scenario.includes('expanded')) {
      $('ops-tab-maintenance').click();await setState({...ready,required_reason:'model_not_ready',model:{status:'failed',ready:false,error:'Model failure '+ 'long-path/'.repeat(30)}});
      document.querySelectorAll('#ops-maintenance details').forEach(d=>d.open=true);
      $('migrate-target').value='长名称'.repeat(20);file('import-file');file('recover-file');
    }
    await settle();
    assert(document.documentElement.scrollWidth<=innerWidth,'no viewport overflow');
    for(const card of document.querySelectorAll('#page-ops .ops-card'))if(card.getClientRects().length)assert(card.scrollWidth<=card.clientWidth+1,'card no overflow');
    assert($('ops-tabs').getBoundingClientRect().width<=innerWidth,'tabs fit');
    const cards=[...document.querySelectorAll('#page-ops .ops-card')].filter(c=>c.getClientRects().length);
    const left=cards[0].getBoundingClientRect().left, right=cards[0].getBoundingClientRect().right;
    for(const card of cards){const r=card.getBoundingClientRect();assert(Math.abs(r.left-left)<1&&Math.abs(r.right-right)<1,'uniform card edges');}
    if($('page-ops').clientWidth>760){const x=cards[0].querySelector('.ops-card-content').getBoundingClientRect().left;for(const card of cards)assert(Math.abs(card.querySelector('.ops-card-content').getBoundingClientRect().left-x)<1,'uniform wide content column');}
    if(!$('ops-settings').hidden) {
      for(const card of cards) {
        const heading=card.querySelector('.ops-card-heading').getBoundingClientRect();
        const content=card.querySelector('.ops-card-content').getBoundingClientRect();
        assert(content.top>=heading.bottom,'settings content below heading');
        assert(Math.abs(content.left-heading.left)<1&&Math.abs(content.right-heading.right)<1,'settings content fills heading width');
      }
      const port=$('config-port').getBoundingClientRect(), language=$('config-language').getBoundingClientRect();
      if($('page-ops').clientWidth>480)assert(Math.abs(port.top-language.top)<1&&Math.abs(port.width-language.width)<1,'equal desktop field columns');
      else assert(language.top>port.bottom&&Math.abs(port.left-language.left)<1,'narrow fields stacked');
      input('model-proxy-enabled',true);
      await settle();
      assert(!$('model-proxy-fields').hidden&&$('model-proxy-url').getBoundingClientRect().right<=right,'proxy expands within card');
      assert(document.documentElement.scrollWidth<=innerWidth,'expanded proxy no overflow');
    }

    result.textContent='PASS: '+scenario;return;
  }
  if(scenario==='ops-restart'||scenario==='ops-shutdown'||scenario==='ops-restart-timeout') {
    $('ops-tab-maintenance').click();await setState(ready);
    const original=window.confirm;window.confirm=()=>true;
    if(scenario==='ops-restart-timeout') {
      const realNow=Date.now, realTimeout=window.setTimeout;let elapsed=0;
      Date.now=()=>realNow()+elapsed;
      window.setTimeout=(fn,ms,...args)=>{if(ms===2000){elapsed+=25000;ms=0;}return realTimeout(fn,ms,...args);};
      $('btn-restart').click();
      for(let i=0;i<100;i++) {
        if(writes.length)down=true;
        if(!$('restart-poll-retry').hidden)break;
        await new Promise(resolve=>realTimeout(resolve,10));
      }
      Date.now=realNow;window.setTimeout=realTimeout;
      assert(!$('restart-poll-retry').hidden&&$('btn-restart').disabled,'timeout ends automatic reconnect and locks writes');
      assert(writes.length===1,'timeout does not resubmit restart');
      down=false;$('restart-poll-retry').click();await waitFor(()=>$('restart-result').textContent.includes('服务当前可访问'),'manual reconnect');
      window.confirm=original;result.textContent='PASS: restart timeout and manual connection recovery';return;
    }
    $(scenario==='ops-restart'?'btn-restart':'btn-shutdown').click();
    await waitFor(()=>writes.length===1,'single process control request');
    if(scenario==='ops-restart') {
      await waitFor(()=>$('restart-result').textContent.includes('服务当前可访问'),'bounded reconnect');
      assert($('btn-restart').disabled,'normal resumed state still gates restart');
    } else {
      await waitFor(()=>$('restart-result').textContent.includes('terminal'),'shutdown feedback');
      await ops.refreshMaintenance();assert($('btn-maintenance-enter').disabled&&$('btn-model-save').disabled,'shutdown keeps controls blocked');
    }
    window.confirm=original;result.textContent='PASS: '+scenario;return;
  }
  assert($('btn-model-save').disabled,'clean config cannot save');
  holdMaintenance=true;const firstPoll=ops.refreshMaintenance(), secondPoll=ops.refreshMaintenance();
  assert(firstPoll===secondPoll,'overlapping poll reuses one request');holdMaintenance();holdMaintenance=null;await firstPoll;
  const radio=document.querySelector('#theme-select input[value=dark]');radio.click();assert(document.documentElement.dataset.theme==='dark'&&$('btn-model-save').disabled,'theme independent of config dirty');
  document.querySelector('#theme-select input[value=light]').click();
  assert($('model-proxy-url').value===''&&!$('model-proxy-saved').textContent.includes('***'),'credentials never echoed');
  input('config-port','0');assert($('btn-model-save').disabled,'invalid port blocked');
  input('config-port','1414');assert(!$('btn-model-save').disabled,'valid dirty enabled');
  $('ops-tab-maintenance').click();assert(snapshot.state==='normal'&&!$('ops-maintenance').hidden,'tab does not enter maintenance');
  assert($('btn-sync').disabled,'normal blocks sync');
  $('ops-tab-settings').click();await ops.refreshMaintenance();assert($('config-port').value==='1414','poll retains draft');
  input('model-proxy-enabled',true);assert(!$('model-proxy-fields').hidden,'proxy expands');
  input('model-proxy-url','http://***@proxy:8080');assert($('btn-model-save').disabled,'masked URL rejected');
  input('model-proxy-url','');
  const originalConfirm=window.confirm;window.confirm=()=>false;input('config-local-only',false);assert(!$('config-local-only-warning').hidden,'LAN warning immediate');
  $('btn-model-save').click();await settle();assert(saved.length===0,'LAN cancel preserves draft');
  window.confirm=()=>true;holdSave=true;$('btn-model-save').click();
  await waitFor(()=>typeof holdSave==='function','save held');await ops.refreshMaintenance();assert($('btn-model-save').disabled&&$('btn-maintenance-enter').disabled,'poll cannot unlock save');
  holdSave();holdSave=null;await waitFor(()=>!$('ops-config-fields').disabled,'saved');
  assert(!('url' in saved[0].proxy),'blank proxy omitted');assert($('btn-model-save').disabled,'saved becomes clean');assert(!$('config-restart-notice').hidden,'known restart changes flagged');
  // Language applies on refresh only; the restart notice never mentions it.
  const pageLang=document.documentElement.lang||'zh-CN';
  const otherLang=pageLang==='en'?'zh-CN':'en';
  input('config-language',otherLang);$('btn-model-save').click();
  await waitFor(()=>!$('config-language-refresh').hidden&&!$('ops-config-fields').disabled,'language refresh notice');
  assert($('config-restart-text').textContent===t('ops.config.restartNeeded'),'restart notice excludes language');
  // Language together with a port change flags both, restart still port-only.
  input('config-port','1416');$('btn-model-save').click();
  await waitFor(()=>!$('ops-config-fields').disabled&&$('config-port').value==='1416','combined save');
  assert(!$('config-language-refresh').hidden&&!$('config-restart-notice').hidden,'combined save shows both notices');
  // The refresh button honours the unsaved-draft guard before reloading.
  input('config-port','1515');window.confirm=()=>false;$('config-language-refresh-btn').click();await settle();
  assert($('config-port').value==='1515','refresh aborted while config dirty');window.confirm=()=>true;
  input('config-port','1414');input('config-language','zh-CN');$('btn-model-save').click();
  await waitFor(()=>$('btn-model-save').disabled,'baseline restored');
  input('config-language','en');saveError=true;$('btn-model-save').click();await waitFor(()=>$('model-config-result').textContent.includes('disk error'),'save error');
  assert($('config-language').value==='en'&&!$('btn-model-save').disabled,'failed save preserves editable draft');saveError=false;
  await setState({...ready,required_reason:'model_not_ready',model:{status:'failed',ready:false,error:'missing'}});retryError=true;$('btn-model-save').click();
  await waitFor(()=>$('model-config-result').textContent.includes('模型重试未成功'),'save and retry differentiated');
  assert($('model-config-result').textContent.startsWith('配置已保存'),'persist succeeded despite retry failure');retryError=false;
  $('ops-tab-maintenance').click();
  for(const [state,sync,exit,restart,retry] of [
    [normal,false,false,false,false], [ready,true,true,true,false],
    [{...ready,required_reason:'model_not_ready',action:'restart',model:{status:'failed'}},false,false,false,false],
    [{...ready,error:'sticky'},true,false,true,false],
    [{...ready,required_reason:'model_not_ready',error:'sticky',model:{status:'failed'}},false,false,true,true],
    [{...ready,required_reason:'model_not_ready',model:{status:'downloading'}},false,false,true,false],
    [{...ready,action:'import'},false,false,false,false],
  ]) {await setState(state);assert($('btn-sync').disabled===!sync&&$('btn-maintenance-exit').disabled===!exit&&$('btn-restart').disabled===!restart&&$('btn-model-retry').disabled===!retry,'state gates '+JSON.stringify(state));}
  await setState({...ready,required_reason:'model_not_ready',model:{status:'downloading',ready:false,file:'weights.bin',completed:40,total:100,percent:40}});
  assert($('model-progress').value===40&&$('model-progress-detail').textContent.includes('40.0%'),'known byte progress');
  await setState({...ready,required_reason:'model_not_ready',model:{status:'failed',ready:false,error:'<img src=x onerror=bad>'}});
  assert(!$('model-progress').hasAttribute('value')&&!$('model-error').querySelector('img'),'unknown progress and literal error');
  down=true;await ops.refreshMaintenance();assert($('btn-sync').disabled&&$('btn-model-save').disabled&&$('btn-status').textContent===t('status.unavailable'),'offline fail closed');down=false;
  await setState(ready);holdSync=true;$('btn-sync').click();await waitFor(()=>typeof holdSync==='function','sync held');await ops.refreshMaintenance();assert($('btn-sync').disabled&&$('btn-maintenance-exit').disabled,'local action lock survives idle poll');holdSync();holdSync=null;await waitFor(()=>!$('btn-sync').disabled,'sync finished');
  $('ops-clear').open=true;input('clear-ns','a');$('btn-clear').click();await waitFor(()=>$('clear-modal').style.display==='flex','clear opened');
  assert($('app-shell').inert&&document.activeElement===$('clear-confirm-input'),'confirmation focus and inert');
  input('clear-confirm-input','bad');assert($('btn-clear-confirm').disabled,'wrong code disabled');input('clear-ns','b');await code();await waitFor(()=>clearScope!==null,'clear sent');
  assert(clearScope.endsWith('namespace=a'),'confirmed target is immutable');await waitFor(()=>!$('btn-sync').disabled,'clear released');
  $('ops-recover').open=true;file('recover-file');$('btn-recover').click();await waitFor(()=>$('clear-modal').style.display==='flex','recover opened');
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));await settle();assert(recoverPosts===0&&!$('app-shell').inert,'cancel recover');
  $('btn-recover').click();await waitFor(()=>$('clear-modal').style.display==='flex','recover reopened');await setState(normal);await code();
  await waitFor(()=>$('recover-result').textContent.includes('状态已变化'),'confirm rechecks current state');assert(recoverPosts===0,'stale confirmation cannot execute');await setState(ready);
  $('btn-recover').click();await code();await waitFor(()=>recoverPosts===1&&$('recover-result').textContent.includes('Recovered'),'recover completed');
  await waitFor(()=>!$('btn-sync').disabled,'recover release');
  $('ops-import').open=true;file('import-file');jobReadFail=true;$('btn-import').click();await waitFor(()=>!$('import-poll-retry').hidden,'query retry visible');
  assert(importPosts===1&&$('btn-import').disabled&&$('btn-maintenance-exit').disabled,'uncertain job cannot resubmit');
  $('ops-import').open=false;await settle();assert($('ops-import').open,'running job result cannot be collapsed');
  jobReadFail=false;$('import-poll-retry').click();await waitFor(()=>$('import-result').textContent.includes('Imported'),'query resumes');assert(importPosts===1,'query retry does not reupload');
  await waitFor(()=>!$('btn-sync').disabled,'job release');$('ops-import').open=false;await settle();assert(!$('import-result').textContent,'explicit collapse clears result');
  $('ops-export').open=true;$('btn-export-full').click();await waitFor(()=>$('export-result').textContent.includes('(4 records)'),'export count');
  await waitFor(()=>!$('btn-sync').disabled,'export release');$('ops-migrate').open=true;input('migrate-source','a');input('migrate-target','new-space');$('btn-migrate').click();await waitFor(()=>$('migrate-result').textContent.includes('Migrated'),'migration result retained');
  await waitFor(()=>!$('btn-sync').disabled,'migration release');
  window.confirm=originalConfirm;result.textContent='PASS: config drafts, gates, credentials, confirmation snapshots, tasks and transfer';
}
