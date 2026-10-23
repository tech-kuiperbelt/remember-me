// Exercise the real UI using only local API fetch-boundary responses.
async function runVersionUpdateRegression(scenario) {
  await waitFor(() => $('version-entry') && $('version-popover') && $('btn-status').dataset.state !== 'loading', 'version mount');
  const entry = $('version-entry'), panel = $('version-popover'), refresh = $('version-refresh');
  let revision = 10;
  const release = (version, values = {}) => ({tag:'v'+version, version,
    url:'https://github.com/tech-kuiperbelt/remember-me/releases/tag/v'+version,
    published_at:'2026-10-23T01:00:00Z',
    release:{version, new:[], improvements:[], fixes:[], upgrade:[], breaking:[], ...values}});
  const latest = release('1.2.0', {new:['A new feature'], upgrade:['Back up data before upgrade.'], breaking:['Intermediate storage format changed.']});
  function state(status = 'UPDATE_AVAILABLE', current = '1.0.0') {
    const target = ['UPDATE_AVAILABLE','UPDATE_RECOMMENDED','UP_TO_DATE'].includes(status) ? structuredClone(latest) : null;
    return {current:{version:current, install_method:'UNKNOWN', release:null, error_code:current ? null : 'local_release_invalid'},
      check:{status, checking:status === 'CHECKING', revision:revision++, last_attempt_at:'2026-10-23T01:00:00Z',
        last_success_at:target ? '2026-10-23T01:00:00Z' : null, error_code:null, next_allowed_at:null},
      latest:target, notes_scope:target ? (status === 'UPDATE_AVAILABLE' ? 'upgrade_range' : 'latest_only') : 'none',
      notes_complete:status === 'UPDATE_AVAILABLE' ? true : null,
      releases:status === 'UPDATE_AVAILABLE' ? [structuredClone(latest),release('1.1.0', {breaking:['Middle release breaking change.']})] : [],
      release_errors:[], notes_error_code:null};
  }
  let server = state(), post;
  S.setOverride((url, init) => {
    if (url === '/api/version-update') return json(server);
    if (url === '/api/version-update/check') return post ? post(init) : json(server, 202);
  });
  async function load(data) {
    server = data;
    document.dispatchEvent(new Event('visibilitychange'));
    await settle(50);
  }
  const visibleText = () => panel.textContent;
  const escape = () => document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true,cancelable:true}));
  const open = () => { if(panel.hidden) entry.click(); };
  const assertState = (expected, version, badge) => {
    assert(entry.dataset.state === expected, 'state '+expected);
    assert($('version-current').textContent === version, 'current version independent of remote state');
    assert($('version-badge').textContent === badge, 'badge '+badge);
  };
  await load(server); open(); await settle(30);
  if (scenario === 'version-functional') {
    const hash = location.hash, page = document.querySelector('.nav-item[aria-current=page]');
    const collapsed = $('app-shell').classList.contains('sidebar-collapsed');
    assert(!panel.hidden && entry.getAttribute('aria-expanded') === 'true', 'entry opens dialog');
    assert($('app-shell').classList.contains('sidebar-collapsed') === collapsed && location.hash === hash, 'entry never toggles/navs');
    assert(page === document.querySelector('.nav-item[aria-current=page]'), 'selected page preserved');
    assertState('UPDATE_AVAILABLE','v1.0.0','Update');
    assert([...panel.querySelectorAll('h3')].map(n=>n.textContent).join(',') === 'v1.2.0,v1.1.0', 'all versions newest first');
    assert(panel.querySelectorAll('.version-notes-breaking').length === 2, 'intermediate breaking notes expanded');
    assert(panel.querySelector('.version-notes-upgrade h4').textContent === t('version.notes.upgrade'), 'upgrade label translated');
    assert(panel.querySelector('.version-release .version-notes').classList.contains('version-notes-upgrade'), 'upgrade notes prioritized');
    assert(panel.querySelectorAll('a').length === 2 && [...panel.querySelectorAll('a')].every(a=>a.rel==='noopener noreferrer'), 'safe per-release links');
    const focusedURL = panel.querySelector('a').href; panel.querySelector('a').focus();
    await load(server); assert(document.activeElement.href===focusedURL,'local snapshot polling preserves link focus');
    for (const [name,badge] of [['UPDATE_RECOMMENDED','Update'], ['UNAVAILABLE','Unavailable'], ['NO_RELEASE','No Release']]) {
      await load(state(name,null)); assertState(name,'Unknown',badge);
      if(name==='UPDATE_RECOMMENDED') {
        assert(visibleText().includes(t('version.message.UPDATE_RECOMMENDED')), 'unknown recommendation/backups without comparison claim');
        assert(panel.querySelectorAll('h3').length===1, 'unknown has latest-only notes');
      } else assert(!panel.querySelector('h3'), 'unknown remote failure/no release has no notes');
    }
    await load(state('NO_RELEASE')); assertState('NO_RELEASE','v1.0.0','No Release');
    await load(state('UP_TO_DATE','1.2.0')); assertState('UP_TO_DATE','v1.2.0','');
    assert(visibleText().includes(t('version.message.UP_TO_DATE')), 'equal version message');
    await load(state('UP_TO_DATE','2.0.0'));
    assert(visibleText().includes(t('version.message.newer')), 'higher local version never urges downgrade');
    const incomplete = state(); incomplete.notes_complete=false;
    incomplete.release_errors=[{tag:'v1.1.5',code:'release_file_missing',url:'https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.1.5'}];
    incomplete.notes_error_code='release_limit_exceeded';
    await load(incomplete);
    assert(visibleText().includes(t('version.incomplete')) && panel.querySelector('.version-incomplete a'), 'historical gap remains visible with links');
    assertState('UPDATE_AVAILABLE','v1.0.0','Update');
    assert(visibleText().includes('v1.2.0'), 'latest retained for historical failure');
    const hostile = state(); hostile.releases=[release('1.2.0',{new:['<img src=https://evil.invalid/pwn onerror=alert(1)>', '**literal markdown**', '<scr'+'ipt>window.pwned=1</scr'+'ipt>']})];
    hostile.releases[0].url='javascript:alert(1)'; await load(hostile);
    assert(!panel.querySelector('img,script,strong') && !window.pwned && visibleText().includes('<img'), 'HTML and Markdown remain text');
    assert(!panel.querySelector('a'), 'unsafe release URL rejected');
    for (const url of ['https://evil.invalid/v1.2.0','https://github.com@evil.invalid/','https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.2.0?token=secret']) {
      hostile.releases[0].url=url; hostile.check.revision=revision++; await load(hostile); assert(!panel.querySelector('a'), 'noncanonical release URL rejected');
    }
    const empty = state(); empty.releases=[release('1.2.0')]; await load(empty);
    assert(visibleText().includes(t('version.emptyNotes')) && !panel.querySelector('.version-notes'), 'empty arrays omitted');
    await load(state('CHECKING',null)); assert(refresh.disabled, 'checking disables refresh');
    await load(state());
    escape(); assert(panel.hidden && document.activeElement===entry, 'Escape restores entry focus');
    open(); $('version-close').click(); assert(panel.hidden, 'close button');
    open(); document.body.click(); assert(panel.hidden, 'outside click');
    // An open Memory editor and its draft are unaffected by the version dialog.
    S.freezeMaintenance();
    const store = await import('/admin/console/static/js/store.js');
    store.setMaintenance({state:'normal'}); store.setDefaultNamespace('ready','a');
    $('memories-create').click(); await waitFor(() => $('edit-text'), 'new memory editor');
    const input = $('edit-text');
    input.value='draft survives software update'; input.dispatchEvent(new Event('input',{bubbles:true}));
    open(); escape(); assert(input.value==='draft survives software update' && $('app-shell').inert,'editor draft and modal lock survive');
    assert(S.calls.filter(c=>c.url.startsWith('/api/version-update')).every(c=>c.method==='GET'), 'inspection never starts remote checks');
  } else if (scenario === 'version-refresh') {
    let resolvePost;
    post=()=>new Promise(resolve=>{resolvePost=resolve;}); S.resetCalls();
    refresh.click(); refresh.click();
    assert(refresh.disabled && S.calls.filter(c=>c.method==='POST').length===1,'duplicate manual refresh disabled');
    const running=state('CHECKING'); running.check.checking=true;
    resolvePost(json(running,202)); await settle(30);
    assert(refresh.disabled && entry.classList.contains('version-checking'),'202 is in progress, not success');
    server=state('UPDATE_RECOMMENDED',null);
    await waitFor(()=>entry.dataset.state==='UPDATE_RECOMMENDED','GET completes manual check');
    assert(!refresh.disabled && S.calls.some(c=>c.method==='GET'),'POST followed by local GET');
    const before=S.calls.filter(c=>c.method==='POST').length;
    let clock=Date.now(), originalNow=Date.now;
    Date.now=()=>clock;
    try {
      const limited=state('UNAVAILABLE'); limited.check.error_code='github_rate_limited';
      limited.check.next_allowed_at=new Date(clock+2000).toISOString();
      post=()=>Promise.resolve(new Response(JSON.stringify(limited),{status:429,headers:{'Retry-After':'4'}}));
      refresh.click(); await settle(30);
      assert(refresh.disabled && !$('version-cooldown').hidden,'429 disables refresh with reason');
      refresh.click(); assert(S.calls.filter(c=>c.method==='POST').length===before+1,'cooldown cannot be bypassed');
      clock+=3000; await load(limited); assert(refresh.disabled,'longer Retry-After wins');
      clock+=2000; limited.check.next_allowed_at=null; await load(limited);
      assert(!refresh.disabled,'expired cooldown clears even at equal revision');
      // HTTP-date Retry-After and longer next_allowed_at are both honored.
      limited.check.revision=revision++; limited.check.next_allowed_at=new Date(clock+8000).toISOString();
      post=()=>Promise.resolve(new Response(JSON.stringify(limited),{status:429,headers:{'Retry-After':new Date(clock+4000).toUTCString()}}));
      refresh.click(); await settle(30); clock+=5000; await load(limited);
      assert(refresh.disabled,'longer snapshot deadline wins');
      clock+=4000; limited.check.next_allowed_at=null; await load(limited); assert(!refresh.disabled,'HTTP-date cooldown expires');
      const historyLimited=state(); historyLimited.notes_complete=false; historyLimited.notes_error_code='release_rate_limited';
      historyLimited.check.next_allowed_at=new Date(clock+3000).toISOString();
      post=()=>json(historyLimited,429); refresh.click(); await settle(30);
      assertState('UPDATE_AVAILABLE','v1.0.0','Update');
      assert(refresh.disabled && panel.querySelector('.version-release') && visibleText().includes(t('version.incomplete')),'429 during history preserves verified latest and gap warning');
    } finally {Date.now=originalNow;}
  } else if (scenario === 'version-errors') {
    for (const code of ['github_unreachable','github_timeout','github_tls_error','github_forbidden','github_service_error','latest_release_missing','latest_release_invalid','latest_release_version_mismatch','check_failed']) {
      const failed=state('UNAVAILABLE'); failed.check.error_code=code; await load(failed);
      assert(!panel.querySelector('.version-release') && !visibleText().includes('A new feature'),'failure clears prior notes '+code);
      assertState('UNAVAILABLE','v1.0.0','Unavailable');
    }
    for (const code of [400,503]) {
      await load(state()); post=()=>Promise.resolve(json({detail:'secret:proxy-password@localhost',response_code:'invalid_request'},code));
      refresh.click(); await waitFor(()=>entry.dataset.state==='UNAVAILABLE','bounded local error');
      assert(visibleText().includes(t('version.localError')) && !visibleText().includes('proxy-password') && !panel.querySelector('.version-release'),'local errors hide notes/raw details');
    }
    await load(state());
    S.setOverride(url=>url==='/api/version-update'?Promise.reject(new Error('secret transport error')):undefined);
    document.dispatchEvent(new Event('visibilitychange')); await settle(30);
    assert(entry.dataset.state==='UNAVAILABLE' && !visibleText().includes('secret'),'GET network failure bounded');
    S.setOverride(url=>url==='/api/version-update'?json({unexpected:'response'}):undefined);
    document.dispatchEvent(new Event('visibilitychange')); await settle(30);
    assert(visibleText().includes(t('version.localError')), 'invalid local snapshot bounded');
  } else if (scenario === 'version-ordering') {
    let resolveOld;
    S.setOverride((url,init)=>{
      if(url==='/api/version-update')return new Promise(resolve=>{resolveOld=resolve;});
      if(url==='/api/version-update/check')return json(server,202);
    });
    document.dispatchEvent(new Event('visibilitychange')); await settle(10);
    const old=state('NO_RELEASE',null); server=state();
    refresh.click(); await settle(30);
    resolveOld(json(old)); await settle(30);
    assertState('UPDATE_AVAILABLE','v1.0.0','Update');
    // A lower revision is stale even when it arrives from a newer request.
    S.setOverride(url=>url==='/api/version-update'?json(old):undefined);
    document.dispatchEvent(new Event('visibilitychange')); await settle(30);
    assertState('UPDATE_AVAILABLE','v1.0.0','Update');
    // An old failed GET must not overwrite the successful POST either.
    let rejectOld;
    S.setOverride((url,init)=>url==='/api/version-update'?new Promise((_,reject)=>{rejectOld=reject;}):json(server,202));
    document.dispatchEvent(new Event('visibilitychange')); await settle(10);
    server=state('UPDATE_RECOMMENDED',null); refresh.click(); await settle(30);
    rejectOld(new Error('old GET failed')); await settle(30);
    assertState('UPDATE_RECOMMENDED','Unknown','Update');
    S.setOverride(url=>url==='/api/version-update/check'
      ? new Response(JSON.stringify(old),{status:429,headers:{'Retry-After':'600'}}) : json(server));
    refresh.click(); await settle(30);
    assertState('UPDATE_RECOMMENDED','Unknown','Update');
    assert(!refresh.disabled,'lower-revision 429 cannot install a stale cooldown');
  } else if (scenario === 'version-polling') {
    const originalTimer=window.setTimeout, originalClear=window.clearTimeout, originalNow=Date.now;
    const timers=new Map(); let timerId=1000000, clock=Date.now();
    window.setTimeout=(fn,delay,...args)=>{
      if(delay===1000||delay===60000) {const id=++timerId; timers.set(id,{fn,delay}); return id;}
      return originalTimer(fn,delay,...args);
    };
    window.clearTimeout=id=>{if(!timers.delete(id))originalClear(id);}; Date.now=()=>clock;
    async function tick(delay) {
      const timer=[...timers.entries()].find(([,value])=>value.delay===delay);
      assert(timer,'scheduled '+delay+'ms poll'); timers.delete(timer[0]); clock+=delay; timer[1].fn(); await settle(30);
    }
    try {
      S.resetCalls(); await load(state()); await tick(60000);
      assert(S.calls.every(c=>c.method==='GET'),'visible polling never POSTs');
      await load(state('CHECKING')); await tick(1000);
      clock+=60001; await tick(1000); assert([...timers.values()].some(v=>v.delay===60000),'short polling bounded');
      Object.defineProperty(document,'hidden',{configurable:true,value:true});
      document.dispatchEvent(new Event('visibilitychange')); assert(!timers.size,'hidden tab cancels polling');
      Object.defineProperty(document,'hidden',{configurable:true,value:false});
      server=state(); document.dispatchEvent(new Event('visibilitychange')); await settle(30); assert(timers.size===1,'visible tab resumes GET');
      window.dispatchEvent(new Event('pagehide')); assert(!timers.size,'pagehide releases timers');
      window.dispatchEvent(new Event('pageshow')); await settle(30); assert(timers.size===1,'bfcache restore resumes polling');
    } finally {
      window.setTimeout=originalTimer; window.clearTimeout=originalClear; Date.now=originalNow; delete document.hidden;
      document.dispatchEvent(new Event('visibilitychange'));
    }
  } else {
    const long = state(); long.releases[0].release.new=Array.from({length:100},(_,i)=>`${i}: ${'Very long text without truncation. '.repeat(10)}`);
    await load(long);
    if(innerWidth<=390||scenario==='version-collapsed') {
      escape(); if(!$('app-shell').classList.contains('sidebar-collapsed'))$('nav').click(); open();
    }
    const box=panel.getBoundingClientRect();
    assert(box.left>=11&&box.right<=innerWidth-11&&box.top>=11&&box.bottom<=innerHeight-11,'popover fits viewport');
    assert(panel.scrollWidth<=panel.clientWidth+1 && panel.querySelector('.version-popover-body').scrollHeight>panel.querySelector('.version-popover-body').clientHeight,'long notes scroll internally');
    const header=panel.querySelector('header').getBoundingClientRect();
    panel.querySelector('.version-popover-body').scrollTop=99999;
    assert(panel.querySelector('header').getBoundingClientRect().top===header.top,'header remains accessible while notes scroll');
    const footer=entry.getBoundingClientRect(), nav=$('nav').getBoundingClientRect();
    assert(footer.bottom<=nav.bottom && footer.top>=nav.top,'footer stays in sidebar');
    assert($('nav').querySelector('.nav-items').getBoundingClientRect().bottom<=footer.top,'footer never covers scrolling nav');
    assert(panel.getAttribute('aria-modal')===String(innerWidth<=640) && $('app-shell').inert===(innerWidth<=640),'modal only at narrow width');
    if($('app-shell').classList.contains('sidebar-collapsed')) {
      assert(getComputedStyle($('version-current')).display==='none'&&entry.getAttribute('aria-label').includes('v1.0.0'),'collapsed icon has accessible version');
      assert(Math.abs(box.left-(footer.right+12))<=1 || innerWidth<=640,'anchored to actual collapsed sidebar');
    }
    const token= document.createElement('span'); token.style.color='var(--bg-elevated)'; panel.append(token);
    assert(getComputedStyle(panel).backgroundColor===getComputedStyle(token).color,'background follows real theme token'); token.remove();
    assert(!panel.querySelector('pre,code,input') && !panel.textContent.includes('git pull'),'no Phase D commands/inputs');
    if(scenario==='version-motion') {
      long.check.checking=true; long.check.revision=revision++; await load(long);
      assert(matchMedia('(prefers-reduced-motion: reduce)').matches, 'reduced-motion emulated');
      assert(getComputedStyle(refresh.querySelector('svg')).animationName==='none' && getComputedStyle(entry.querySelector('.version-indicator')).animationName==='none','reduced motion disables spinners');
    }
    // Keep review screenshots at the top of realistic notes after the long-content checks.
    if(scenario!=='version-motion') await load(state());
    panel.querySelector('.version-popover-body').scrollTop=0;
  }
  assert(S.calls.every(c=>!/^https?:/.test(c.url)),'browser never fetches GitHub/raw');
  result.textContent='PASS: '+scenario;
}
