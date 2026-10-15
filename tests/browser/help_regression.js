async function runHelpRegression(scenario) {
  await waitFor(()=>history.state?.rememberNavigation?.page === document.querySelector('.nav [aria-current=page]')?.dataset.page && $('btn-status').dataset.state !== 'loading','navigation bootstrap');
  const visible = topic => !$('help-'+(topic==='home'?'home':'topic-'+topic)).hidden && $('page-help').classList.contains('active');
  const open = topic => { $('nav-help').click(); if(topic!=='home')$('help-entry-'+topic).click(); };
  const fit = label => {
    assert(document.documentElement.scrollWidth<=innerWidth,'viewport fits '+label);
    assert($('content').scrollWidth<=$('content').clientWidth+1,'content fits '+label);
  };
  if(innerWidth<=390&&!$('app-shell').classList.contains('sidebar-collapsed'))$('nav').click();
  if(scenario==='help-model') {
    assert($('page-ops').classList.contains('active')&&!location.hash,'model preparation takes startup priority');
    $('nav-help').click();assert(visible('home'),'Help remains accessible while model unavailable');
    result.textContent='PASS: '+scenario;return;
  }
  if(scenario==='help-deeplink'||scenario==='help-refresh') {
    assert(visible('rules')&&location.hash==='#help/rules','initial rules deep link');
    assert(document.activeElement===$('help-title-rules'),'deep link heading focused');
    if(scenario==='help-refresh'&&!sessionStorage.getItem('help-refresh-stage')) {
      sessionStorage.setItem('help-refresh-stage','1');location.reload();return;
    }
    sessionStorage.removeItem('help-refresh-stage');
    result.textContent='PASS: '+scenario;return;
  }
  if(scenario==='help-invalid') {
    assert(visible('home')&&location.hash==='#help','invalid topic normalized');
    result.textContent='PASS: '+scenario;return;
  }
  open('home');
  assert($('nav-help').getAttribute('aria-current')==='page'&&visible('home'),'Help home and navigation selected');
  assert(!document.querySelector('#page-help [role=tablist]'),'old Help tabs removed');
  const topics=['connect','namespace','rules','memory','retrieval','stats','configuration','troubleshooting'];
  assert(document.querySelectorAll('[data-help-panel]').length===9,'home and eight topics');
  if(scenario==='help-disabled') {
    const ui=await import('/admin/console/static/js/ui.js');
    assert(!ui.debugEnabled()&&!$('nav-help').hidden,'Help available without Debug');
  }
  S.resetCalls();
  for(const topic of topics) {
    open(topic);assert(visible(topic)&&location.hash==='#help/'+topic,'topic '+topic);
    assert(document.activeElement===$('help-title-'+topic),'heading focus '+topic);
    assert(document.querySelectorAll('#page-help [data-help-panel]:not([hidden])').length===1,'single visible panel');
    fit(topic);
  }
  assert(S.calls.every(c=>c.method==='GET'),'Help never performs writes');
  assert(!document.querySelector('#page-help input, #page-help select, #page-help form'),'Help exposes no business operation controls');
  const ids=[...document.querySelectorAll('[id]')].map(n=>n.id);assert(new Set(ids).size===ids.length,'unique DOM IDs');
  for(const link of document.querySelectorAll('[data-help-anchor]'))assert($(link.dataset.helpAnchor),'section anchor exists');

  if(scenario==='help-functional') {
    S.setOverride(url=>url==='/api/maintenance'?json({state:'normal',action:null,error:null,required_reason:null,model:{status:'ready',ready:true}}):undefined);
    const ops=await import('/admin/console/static/js/ops.js');await ops.refreshMaintenance();
    open('home');
    const entry=$('help-entry-stats');entry.focus();entry.scrollIntoView({block:'center'});
    const homeScroll=$('content').scrollTop;entry.click();
    document.querySelector('#help-topic-stats [data-help-home]').click();
    assert(visible('home')&&document.activeElement===entry&&Math.abs($('content').scrollTop-homeScroll)<=1,'breadcrumb restores home scroll and entry focus');
    open('connect');
    history.back();await waitFor(()=>visible('home')&&location.hash==='#help','back to home');
    history.forward();await waitFor(()=>visible('connect')&&location.hash==='#help/connect','forward to topic');
    $('nav-memories').click();await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false','Memories loaded');
    const memoryURL=location.href;
    $('memories-create').click();
    await waitFor(()=>$('edit-text'),'create draft');
    $('edit-text').value='Unsaved Help navigation draft';fire($('edit-text'),'input');
    history.back();await waitFor(()=>$('detail-confirm-discard'),'history leave guard');
    assert(location.href===memoryURL&&$('page-memories').classList.contains('active'),'pending guard keeps URL and page');
    $('detail-confirm-cancel').click();
    assert($('edit-text').value==='Unsaved Help navigation draft'&&location.href===memoryURL,'cancel keeps draft and URL');
    history.back();await waitFor(()=>$('detail-confirm-discard'),'guard retried');
    $('detail-confirm-discard').click();await waitFor(()=>visible('connect')&&location.hash==='#help/connect','confirmed history navigation');
    history.forward();await waitFor(()=>$('page-memories').classList.contains('active'),'forward after accepted leave');
    $('nav-help').click();assert(visible('home'),'sidebar returns home');
    location.hash='help/bogus';await waitFor(()=>location.hash==='#help'&&visible('home'),'manual unknown hash normalized');
    assert(new URLSearchParams(location.search).get('smoke')===scenario,'route retains query parameters');
  }

  open('connect');
  const readMe=new URL('/read-me',location.origin).href;
  assert($('help-read-me-url').textContent===readMe&&$('help-read-me').href===readMe,'actual runtime URL');
  assert($('help-connect-prompt').textContent.includes(readMe)&&$('help-connect-prompt').textContent.includes('连接方式'),'transport chosen from guide');
  const descriptor=Object.getOwnPropertyDescriptor(navigator,'clipboard');let copied='',deny=false;
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{if(deny)throw Error('denied');copied=text;}}});
  try {
    $('help-copy-url').click();await settle();assert(copied===readMe,'copy URL');
    $('help-connect-prompt').closest('details').open=true;
    $('help-copy-connect').click();await settle();assert(copied===$('help-connect-prompt').textContent,'copy full request');
    open('rules');$('help-copy-install').click();await settle();
    assert(copied.startsWith($('help-install-prompt').textContent)&&copied.endsWith($('help-system-prompt').textContent)&&copied.includes('主动回忆')&&copied.includes('8. 事实与安全'),'combined copy complete');
    $('help-system-prompt').closest('details').open=true;
    $('help-copy-system').click();await settle();assert(copied===$('help-system-prompt').textContent,'copy rules alone');
    $('help-system-prompt').closest('details').open=false;deny=true;
    $('help-copy-install').click();await settle();
    assert($('help-system-prompt').closest('details').open&&$('help-install-prompt').closest('details').open,'copy failure exposes both sources');
    assert([...document.querySelectorAll('.toast')].some(el=>el.textContent.includes('手动复制保存请求')),'copy failure feedback');
  } finally {
    if(descriptor)Object.defineProperty(navigator,'clipboard',descriptor);else delete navigator.clipboard;
    document.querySelectorAll('#page-help details').forEach(d=>d.open=false);
    document.querySelectorAll('.toast').forEach(el=>el.remove());
  }
  const clients=[...document.querySelectorAll('.help-client-guides details')];
  clients.forEach(el=>el.open=true);assert(clients.length===5&&clients.every(el=>el.querySelector('a')?.protocol==='https:'),'five client instructions kept');fit('expanded clients');clients.forEach(el=>el.open=false);
  open('troubleshooting');
  const anchor=document.querySelector('[data-help-anchor="help-faq-service"]');anchor.click();
  assert(location.hash==='#help/troubleshooting'&&document.activeElement===$('help-faq-service'),'section scroll leaves route intact and moves focus');
  document.querySelector('#help-faq-service summary').click();assert(document.querySelector('#help-faq-service details').open,'FAQ expands');
  document.querySelector('#help-faq-service details').open=false;
  open('home');
  // Sidebar width, not viewport width, determines columns.
  for(let i=0;i<2;i++) {
    $('nav').click();await settle();
    const columns=getComputedStyle(document.querySelector('.help-start-grid')).gridTemplateColumns.split(' ').length;
    assert(columns===($('page-help').clientWidth<800?1:3),'container-driven columns');fit('sidebar '+i);
  }
  const target=scenario.startsWith('help-layout-')?scenario.split('-')[2]:'home';
  open(target);$('content').scrollTop=0;await settle();fit('screenshot');
  result.textContent='PASS: '+scenario;
}
