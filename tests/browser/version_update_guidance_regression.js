// Phase D uses real modules and DOM; only the HTTP boundary and clipboard are mocked.
async function runVersionGuidanceRegression(scenario) {
  await waitFor(()=>$('version-popover') && $('memories-list').getAttribute('aria-busy')==='false','guidance bootstrap');
  const docker=document.documentElement.dataset.deploymentMode==='docker';
  let revision=100, method=docker?'DOCKER':'SOURCE';
  const release={tag:'v1.2.3',version:'1.2.3',url:'https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.2.3',
    published_at:'2026-10-23T00:00:00Z',release:{version:'1.2.3',new:['Feature'],improvements:[],fixes:[],upgrade:['Back up'],breaking:['Read compatibility notes']}};
  function state(status='UPDATE_AVAILABLE') {
    const latest=['UPDATE_AVAILABLE','UPDATE_RECOMMENDED','UP_TO_DATE'].includes(status)?structuredClone(release):null;
    return {current:{version:status==='UPDATE_RECOMMENDED'?null:'1.0.0',install_method:method,release:null,error_code:null},
      check:{status,checking:false,revision:revision++,last_attempt_at:null,last_success_at:null,error_code:status==='UNAVAILABLE'?'github_unreachable':null,next_allowed_at:null},
      latest,notes_scope:latest?'latest_only':'none',notes_complete:null,releases:[],release_errors:[],notes_error_code:null};
  }
  const normal={state:'normal',action:null,error:null,required_reason:null,model:{ready:true,status:'ready'}};
  let server=state(), config={port:1314,language:document.documentElement.lang,local_only:true,debug:true,debug_effective:true,
    auto_download_model:!docker,proxy:{enabled:false,url:'http://***@proxy:8080'}};
  let holdConfig=false, resolveConfig, configGets=0, holdCheck=false, resolveCheck, failNextSave=false;
  const saves=[], checks=[];
  S.setOverride((url,init)=>{
    if(url==='/api/version-update')return json(server);
    if(url==='/api/version-update/check') {
      checks.push(structuredClone(config.proxy));
      if(holdCheck)return new Promise(resolve=>{resolveCheck=resolve;});
      return json(server,202);
    }
    if(url==='/api/maintenance')return json(normal);
    if(url==='/api/config') {
      if(init?.method==='POST') {
        if(failNextSave) {failNextSave=false;return json({detail:'test save refused'},400);}
        const payload=JSON.parse(init.body); saves.push(payload);
        config={...config,...payload,proxy:{...config.proxy,...payload.proxy}};
        if(config.proxy.url)config.proxy.url='http://***@proxy:8080';
        return json(config);
      }
      configGets++;
      if(holdConfig)return new Promise(resolve=>{resolveConfig=()=>resolve(json(config));});
      return json(config);
    }
  });
  async function load(data) {server=data;document.dispatchEvent(new Event('visibilitychange'));await settle(50);}
  const open=async()=>{if($('version-popover').hidden)$('version-entry').click();await settle(30);};
  const action=key=>$('version-popover').querySelector(`[data-version-action="${key}"]`);
  const command=kind=>$('version-popover').querySelector(`[data-command="${kind}"] code`)?.textContent;
  const commands=()=>[...$('version-popover').querySelectorAll('.version-command code')].map(n=>n.textContent);
  const source='git pull --ff-only\nbash bin/install.sh\nbash bin/start.sh';
  const image='ghcr.io/tech-kuiperbelt/remember-me:1.2.3';
  const compose=`export REMEMBER_ME_IMAGE=${image}\ndocker compose -f compose.yaml up -d --pull always --wait --wait-timeout 300`;
  const run=`export REMEMBER_ME_IMAGE=${image}\ndocker pull --platform linux/amd64 "$REMEMBER_ME_IMAGE"`;
  const input=(id,value)=>{const node=$(id);if(node.type==='checkbox')node.checked=value;else node.value=value;fire(node,'input');};
  await load(server); await open();
  if(scenario==='version-guide-source') {
    assert(command('source')===source && !command('compose'),'SOURCE safe commands');
    assert($('version-popover').textContent.includes(t('version.guide.sourceContext')),'current branch/unreleased declaration');
    assert($('version-popover').textContent.includes(t('version.guide.sourceSafety')),'dirty/detached/upstream guidance');
    assert($('version-popover').textContent.includes(t('version.guide.backup')),'stop/full backup/read notes');
    await load(state('UPDATE_RECOMMENDED'));assert(command('source')===source && $('version-current').textContent==='Unknown','unknown version gets conservative SOURCE guide');
    for(const status of ['UP_TO_DATE','UNAVAILABLE','NO_RELEASE','CHECKING']) {
      await load(state(status)); assert(!commands().length,'no upgrade recommendation in '+status);
    }
  } else if(scenario==='version-guide-docker') {
    assert(command('compose')===compose && command('run')===run,'DOCKER both methods target same complete version');
    assert(!commands().join().includes('--mount') && !commands().join().includes('docker run '),'no guessed mounts/ports/container names');
    assert($('version-popover').textContent.includes(t('version.guide.composeContext')),'original file/env override/saved .env retained');
    assert($('version-popover').textContent.includes(t('version.guide.runRecreate')),'pull is not recreation');
  } else if(scenario==='version-guide-unknown') {
    method='UNKNOWN'; await load(state());
    assert(!commands().length && $('version-current').textContent==='v1.0.0','unknown method does not erase known version');
    assert($('version-popover').textContent.includes(t('version.guide.unknown')),'unknown method avoids guessing');
    action('help').click();await waitFor(()=>$('page-help').classList.contains('active'),'Help navigation');
    assert(location.hash==='#help/configuration' && document.activeElement.id==='help-updates','existing Help topic/section focused');
    for(const link of document.querySelectorAll('[data-update-readme]')) {
      const filename=document.documentElement.lang==='zh-CN'?'README.zh-CN.md':'README.md';
      assert(link.href===`https://github.com/tech-kuiperbelt/remember-me/blob/master/${filename}#${link.dataset.updateReadme}`,'canonical localized README');
    }
  } else if(scenario==='version-guide-safety') {
    method='DOCKER';
    for(const version of ['1.2.3; touch /tmp/no','$(touch /tmp/no)','1.2.3\ncommand','latest','1.2.3-rc.1']) {
      const invalid=state(); invalid.latest.version=version;invalid.latest.tag='v'+version;invalid.latest.release.version=version;
      await load(invalid);assert(!commands().length,'reject command injection/invalid version '+version);
    }
    const mismatch=state();mismatch.latest.tag='v1.2.4';await load(mismatch);assert(!commands().length,'reject mismatched tag');
    const releaseMismatch=state();releaseMismatch.latest.release.version='1.2.4';await load(releaseMismatch);assert(!commands().length,'reject mismatched JSONC');
    const hostile=state();hostile.latest.release.upgrade=['$(malicious-command)'];await load(hostile);
    assert(command('compose')===compose && !commands().join().includes('malicious-command'),'notes never become commands');
    await load(state('UNAVAILABLE'));assert(!commands().length && action('proxy'),'failure drops old target commands');
  } else if(scenario==='version-guide-copy') {
    const original=Object.getOwnPropertyDescriptor(navigator,'clipboard'), copied=[];
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{copied.push(text);}}});
    try {
      action('copy-source').focus();action('copy-source').click();await settle(20);
      assert(copied[0]===source,'copy exact SOURCE commands');
      const same=server;await load(same);assert(document.activeElement.dataset.versionAction==='copy-source','polling preserves copy button focus');
      method='DOCKER';await load(state());action('copy-compose').click();await settle(20);assert(copied[1]===compose,'copy target Compose commands');
      action('copy-run').click();await settle(20);assert(copied[2]===run,'copy target pull only');
      navigator.clipboard.writeText=async()=>{throw new Error('private failure');};action('copy-run').click();await settle(20);
      assert($('version-popover').textContent.includes(t('version.guide.copyFailed'))&&!$('version-popover').textContent.includes('private failure'),'bounded clipboard failure with manual fallback');
      assert(S.calls.every(c=>c.method==='GET'),'copy never calls backend writes');
    } finally {if(original)Object.defineProperty(navigator,'clipboard',original);else delete navigator.clipboard;}
  } else if(scenario.startsWith('version-proxy-')) {
    await load(state('UNAVAILABLE'));
    if(scenario==='version-proxy-navigation') {
      holdConfig=true;action('proxy').click();await waitFor(()=>resolveConfig,'config pending');
      $('nav-help').click();resolveConfig();await settle(50);holdConfig=false;
      assert($('page-help').classList.contains('active') && document.activeElement.id==='help-title','late config load cannot steal focus');
      $('nav-memories').click();
      const store=await import('/admin/console/static/js/store.js');store.setMaintenance(normal);store.setDefaultNamespace('ready','a');
      $('memories-create').click();await waitFor(()=>$('edit-text'),'dirty editor');input('edit-text','draft must survive');
      await open();action('proxy').click();await waitFor(()=>$('detail-confirm'),'leave guard');
      assert($('page-memories').classList.contains('active'),'proxy navigation waits for leave guard');
      $('detail-confirm-cancel').click();assert($('edit-text').value==='draft must survive','cancel retains draft');
      await open();action('proxy').click();$('detail-confirm-discard').click();
      await waitFor(()=>document.activeElement.id==='model-proxy-enabled','accepted navigation focus');
      assert(!$('ops-settings').hidden && !saves.length,'proxy does not auto-save/enable');
    } else {
      action('proxy').click();await waitFor(()=>document.activeElement.id==='model-proxy-enabled','direct Settings proxy focus');
      assert($('version-popover').hidden && !$('ops-settings').hidden && !$('app-shell').inert,'dialog releases background and opens Settings');
      assert(!saves.length && !$('model-proxy-enabled').checked,'no automatic changes');
      assert($('model-proxy-enabled').getClientRects().length>0,'proxy visible in '+method);
      if(docker) {
        assert(!$('config-port').getClientRects().length && !$('model-auto-download').getClientRects().length,'Docker fixed controls stay hidden');
        assert($('model-proxy-help').textContent.includes('SOURCE'),'shared proxy help clarifies model scope');
      }
      input('model-proxy-enabled',true);input('model-proxy-url','http://***@proxy:8080');
      assert($('btn-model-save').disabled,'redacted address cannot be submitted');
      input('model-proxy-url','socks5://proxy:1080');assert($('btn-model-save').disabled,'unsupported proxy blocked');
      input('model-proxy-url','http://test-user:test-pass@proxy.example:8080');
      assert(!$('btn-model-save').disabled,'valid proxy draft enables save');
      if(!docker)input('config-port','1414');
      $('nav-memories').click();await open();action('proxy').click();
      await waitFor(()=>document.activeElement.id==='model-proxy-enabled','return to proxy draft');
      assert($('model-proxy-url').value==='http://test-user:test-pass@proxy.example:8080' && configGets===1,'navigation preserves draft, no reload');
      failNextSave=true;$('btn-model-save').click();
      await waitFor(()=>$('model-config-result').classList.contains('is-error')&&!$('ops-config-fields').disabled,'failed save');
      assert($('model-proxy-url').value==='http://test-user:test-pass@proxy.example:8080' && !$('btn-model-save').disabled && !saves.length,'save failure retains draft for retry');
      $('btn-model-save').click();await waitFor(()=>saves.length===1&&!$('ops-config-fields').disabled,'proxy saved outside maintenance');
      assert(saves[0].proxy.enabled && saves[0].proxy.url==='http://test-user:test-pass@proxy.example:8080','existing proxy API fields');
      if(docker)assert(Object.keys(saves[0]).sort().join(',')==='debug,language,proxy','Docker save only releases proxy restrictions');
      assert($('model-proxy-url').value==='' && !$('model-proxy-saved').textContent.includes('test-pass'),'saved credentials not echoed');
      input('model-proxy-enabled',false);$('btn-model-save').click();await waitFor(()=>saves.length===2&&!$('ops-config-fields').disabled,'proxy disabled');
      assert(saves[1].proxy.enabled===false&&!('url' in saves[1].proxy),'disable retains saved address');
      input('model-proxy-enabled',true);$('btn-model-save').click();await waitFor(()=>saves.length===3&&!$('ops-config-fields').disabled,'proxy re-enabled with blank URL');
      assert(!('url' in saves[2].proxy),'blank URL preserves credentials');
      holdCheck=true;await open();$('version-refresh').click();await waitFor(()=>resolveCheck,'manual check after proxy save');
      assert(checks.at(-1).enabled,'manual check sees saved config at boundary');
      server=state();server.check.checking=true;resolveCheck(json(server,202));await settle(20);
      server=state('UPDATE_RECOMMENDED');await waitFor(()=>$('version-entry').dataset.state==='UPDATE_RECOMMENDED','GET completes saved-proxy check');
      assert(!S.calls.some(c=>c.url==='/api/model/retry'),'saving proxy with ready model never retries download');
    }
  } else {
    if(innerWidth<=390) {$('version-close').click();if(!$('app-shell').classList.contains('sidebar-collapsed'))$('nav').click();await open();}
    const panel=$('version-popover'), box=panel.getBoundingClientRect(), body=panel.querySelector('.version-popover-body');
    assert(box.left>=11&&box.right<=innerWidth-11&&box.top>=11&&box.bottom<=innerHeight-11,'guides fit viewport');
    assert(panel.scrollWidth<=panel.clientWidth+1 && body.scrollWidth<=body.clientWidth+1,'commands wrap without horizontal overflow');
    const top=$('version-close').getBoundingClientRect().top;body.scrollTop=99999;
    assert($('version-close').getBoundingClientRect().top===top,'header accessible during guide scrolling');
    assert(command('compose')===compose && command('run'),'Docker guidance available in all themes/layouts');
    body.scrollTop=panel.querySelector('.version-guide').offsetTop-body.offsetTop;
  }
  assert(S.calls.every(c=>!/^https?:/.test(c.url)),'no GitHub fetch from browser');
  assert(!S.calls.some(c=>/upgrade|backup|command|exec/.test(c.url)),'no execution API');
  result.textContent='PASS: '+scenario;
}
