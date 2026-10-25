// Injected only by the opt-in integration runner. Product routes are real HTTP;
// /__phase_e/* controls only the separate GitHub TLS fixture, never product state.
addEventListener('DOMContentLoaded', async () => {
  const result=document.createElement('pre');result.id='smoke-result';document.body.append(result);
  const $=id=>document.getElementById(id), assert=(value,message)=>{if(!value)throw Error(message);};
  const wait=async(fn,label)=>{
    const deadline=Date.now()+15000;let lastError;
    for(;;) {
      try {if(await fn())return;} catch(error) {lastError=error;}
      if(Date.now()>deadline)throw Error(label+(lastError?' ('+lastError.message+')':''));
      await new Promise(r=>setTimeout(r,60));
    }
  };
  const get=async path=>{const r=await fetch(path,{cache:'no-store'});return r.json();};
  const post=async(path,data={})=>{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream'},body:JSON.stringify(data)});return r.json();};
  const mark=message=>(window.__phase_e_passes||=[]).push(message);
  const expected=window.__phase_e;
  const state=()=>get('/api/version-update');
  const mode=name=>post('/__phase_e/case',{mode:name});
  const open=()=>{if($('version-popover').hidden)$('version-entry').click();};
  const uiSync=async status=>{
    document.dispatchEvent(new Event('visibilitychange'));
    await wait(()=>$('version-entry').dataset.state===status&&!$('version-entry').classList.contains('version-checking'),'UI state '+status);
    open();
  };
  const check=async wanted=>{
    open();await wait(()=>!$('version-refresh').disabled,'refresh enabled');$('version-refresh').click();
    await wait(async()=>{const s=await state();return !s.check.checking&&s.check.status===wanted;},'backend '+wanted);
    // Completion must be delivered by the real frontend GET polling after POST.
    await wait(()=>$('version-entry').dataset.state===wanted&&!$('version-refresh').disabled,'frontend polling '+wanted);
    return state();
  };
  const mcp=async(name,args)=>{
    const r=await post('/mcp',{jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:args}});
    assert(!r.error&&!r.result.isError,'MCP '+name);return JSON.parse(r.result.content[0].text);
  };
  const proxy=async(url,viaShortcut=false)=>{
    open();
    if(viaShortcut)$('version-popover').querySelector('[data-version-action=proxy]').click();
    else {$('version-close').click();$('nav-ops').click();}
    await wait(()=>!$('ops-config-fields').disabled,'config loaded');
    assert(!$('ops-settings').hidden,'Settings tab');
    if(viaShortcut)await wait(()=>document.activeElement.id==='model-proxy-enabled','proxy focused after navigation');
    $('model-proxy-enabled').checked=true;$('model-proxy-url').value=url;
    $('model-proxy-url').dispatchEvent(new Event('input',{bubbles:true}));
    assert(!$('btn-model-save').disabled,'proxy draft savable');$('btn-model-save').click();
    await wait(()=>!$('ops-config-fields').disabled&&$('btn-model-save').disabled,'proxy persisted');
    assert($('model-proxy-url').value==='','credentials not filled back');
    open();
  };
  try {
    await wait(()=>$('version-popover')&&$('memories-list').getAttribute('aria-busy')==='false','HTTP app mounted');
    const wanted=expected.version?'UPDATE_AVAILABLE':'UPDATE_RECOMMENDED';
    await wait(async()=>!(await state()).check.checking,'startup check');let s=await state();
    assert(s.check.status===wanted,'startup target '+JSON.stringify(s.check));await uiSync(wanted);
    assert(s.current.version===expected.version&&s.current.install_method===expected.method,'local file identity');
    assert($('version-current').textContent===(expected.version?'v'+expected.version:'Unknown'),'sidebar local version');
    assert(s.latest.version==='1.3.0'&&s.latest.release.upgrade.length,'Latest -> TLS raw JSONC');
    if(expected.version)assert(s.releases.map(r=>r.version).join(',')==='1.3.0,1.2.0,1.1.0','all historical JSONC versions');
    else assert(s.notes_scope==='latest_only'&&s.releases.length===0,'Unknown has no historical range');
    mark('startup/local identity/Latest/raw/history/UI');
    let seen=(await get('/__phase_e/state')).requests.length;
    await state();assert((await get('/__phase_e/state')).requests.length===seen,'GET never checks GitHub');
    if(expected.version) {
      await mode('equal');s=await check('UP_TO_DATE');assert(s.latest.version===expected.version,'equal version');
      await mode('older');s=await check('UP_TO_DATE');assert(s.latest.version==='0.9.0','newer local declaration');
      assert(!$('version-popover').querySelector('.version-command'),'never recommend downgrade');
      await mode('ok');await check(wanted);mark('equal/newer local comparison and no downgrade');
    }
    const added=await mcp('add',{namespace:'phase-e-test',text:'Phase E integration memory 中文',summary:'integration test',metadata:{anchors:['PhaseEIntegration']}});
    const memory=added.id;
    await mode('slow');open();$('version-refresh').click();
    await wait(async()=>(await state()).check.checking,'slow check pending');
    const start=performance.now();await mcp('stats',{});assert(performance.now()-start<1500,'slow network does not block MCP');
    await wait(async()=>!(await state()).check.checking,'slow check finishes');await uiSync(wanted);
    mark('real POST/GET and concurrent MCP responsiveness');
    await mode('gap');s=await check(wanted);
    if(expected.version) {
      assert(s.notes_complete===false&&s.latest.version==='1.3.0'&&s.release_errors.some(e=>e.tag==='v1.2.0'),'historical gap retains latest');
      assert($('version-popover').querySelector('.version-incomplete a'),'gap link visible');
    } else assert(s.notes_scope==='latest_only'&&s.notes_complete===null,'Unknown does not invent gap range');
    mark('history gap degradation');
    for(const name of ['raw_missing','invalid','mismatch','forbidden']) {
      await mode(name);s=await check('UNAVAILABLE');
      assert(s.latest===null&&!$('version-popover').querySelector('.version-release'),'failure clears current notes '+name);
      await mcp('stats',{});
    }
    mark('raw missing/invalid/mismatch and 403 isolated from MCP');
    await mode('ok');await proxy(expected.bad_proxy,true);s=await check('UNAVAILABLE');
    assert(s.check.error_code==='github_tls_error','untrusted TLS rejected '+s.check.error_code);
    await proxy('http://127.0.0.1:1',true);s=await check('UNAVAILABLE');assert(s.check.error_code==='github_unreachable','connection failure');
    await proxy(expected.good_proxy,true);s=await check(wanted);assert(s.latest.version==='1.3.0','saved proxy recovery');
    mark('real Settings save/client replacement/TLS reject/recovery');
    await mode('no_release');s=await check('NO_RELEASE');assert(!s.latest,'proven no release');
    assert($('version-current').textContent===(expected.version?'v'+expected.version:'Unknown'),'No Release keeps local identity');
    await mode('rate');open();$('version-refresh').click();
    await wait(async()=>{const s=await state();return s.check.status==='UNAVAILABLE'&&!s.check.checking;},'rate response');
    await uiSync('UNAVAILABLE');assert($('version-refresh').disabled,'429 cooldown enforced');
    if(expected.clock) {
      await post('/__phase_e/advance',{seconds:3});
      document.dispatchEvent(new Event('visibilitychange'));
    }
    await new Promise(r=>setTimeout(r,2200));await mode('ok');s=await check(wanted);
    mark('No Release/429 cooldown/recovery');
    if(expected.clock) {
      const before=s.check.revision;await post('/__phase_e/advance',{seconds:86400});
      await wait(async()=>{const s=await state();return !s.check.checking&&s.check.revision>before;},'24h automatic check');
      await uiSync(wanted);mark('24h scheduler with fake monotonic clock');
    }
    const matches=await mcp('search_semantic',{namespace:'phase-e-test',query:'Phase E integration memory 中文',k:10});
    assert(JSON.stringify(matches).includes(memory),'memory survives all check failures');
    const config=await get('/api/config');assert(config.proxy.enabled,'proxy persisted');
    if(expected.method==='DOCKER') {
      assert(!config.auto_download_model&&config.local_only,'Docker remains offline/local-only');
      assert(!$('config-port').getClientRects().length&&!$('model-auto-download').getClientRects().length,'fixed settings hidden');
    }
    open();
    const code=[...$('version-popover').querySelectorAll('.version-command code')].map(n=>n.textContent).join('\n');
    assert(code.includes(expected.method==='DOCKER'?'remember-me:1.3.0':'git pull --ff-only'),'method-specific safe guide');
    assert($('version-popover').querySelector('.version-notes-upgrade')&&$('version-popover').querySelector('.version-notes-breaking'),'risk sections visible');
    const wire=await get('/__phase_e/state');
    assert(wire.requests.some(r=>r.host==='api.github.com')&&wire.requests.some(r=>r.host==='raw.githubusercontent.com'),'both HTTPS hosts traversed proxy');
    mark('MCP retrieval/persistence/model policy/guidance/visual content');
    result.textContent='PASS: real Runtime + frontend + HTTPS CONNECT integration; '+window.__phase_e_passes.join('; ');
  } catch(e) {result.textContent='FAIL: '+e.stack;}
});
