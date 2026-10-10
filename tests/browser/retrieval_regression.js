async function runRetrievalScenario(scenario) {
  if (!scenario.startsWith('retrieval-'))return false;
  await waitFor(()=>$('memories-list').getAttribute('aria-busy')==='false'&&$('memories-namespace').options.length>1,'registry');
  const router=await import('/admin/console/static/js/router.js'),store=await import('/admin/console/static/js/store.js');
  const defaultNS=store.getDefaultNamespace().value, ns=store.getNamespaces()[0]||defaultNS;
  if (scenario==='retrieval-reload'&&sessionStorage.getItem('retrieval-reload-stage')) {
    sessionStorage.removeItem('retrieval-reload-stage');
    assert($('page-memories').classList.contains('active'),'F5 uses normal Memories entry');
    router.switchPage('stats');await waitFor(()=>$('stats-overview'),'Memory after reload');
    assert($('stats-tab-memory').getAttribute('aria-selected')==='true'&&$('stats-namespace').value===''&&$('stats-days-7').getAttribute('aria-pressed')==='true'&&$('stats-keywords-top-5').getAttribute('aria-pressed')==='true','F5 resets all Stats preferences');
    result.textContent='PASS: '+scenario;return true;
  }
  const candidates=JSON.stringify(store.getNamespaces()),categories=JSON.stringify(store.getCategories());
  router.switchPage('stats');await waitFor(()=>$('stats-overview'),'Memory');
  const geometry=()=>{const r=document.querySelector('.stats-view-switch').getBoundingClientRect();return [r.x,r.y,r.width,r.height];};
  if(innerWidth<700&&!document.querySelector('.app-shell').classList.contains('sidebar-collapsed'))$('nav').click();
  const before=JSON.stringify(geometry());
  // Exercise the actual isolated endpoint before installing controlled responses.
  $('stats-tab-retrieval').click();await waitFor(()=>$('stats-retrieval-overview'),'real Retrieval API');
  assert(JSON.stringify(geometry())===before,'real view switch geometry');
  let rating=scenario.startsWith('retrieval-layout-')?7.57:-2.5;
  let mode=scenario.includes('-empty-')?'empty':scenario.includes('-nofeedback-')?'nofeedback':'normal',hold=null,failed=false;
  const fixture=params=>{
    const days=Number(params.get('days')),namespace=params.get('namespace'),timezone=params.get('timezone');
    const start=new Date('2026-10-10T00:00:00Z');start.setUTCDate(start.getUTCDate()-days+1);
    const iso=d=>d.toISOString().slice(0,10),trend=[];
    if(days!==90)for(let i=0;i<days;i++){const d=new Date(start);d.setUTCDate(d.getUTCDate()+i);trend.push({date:iso(d),count:0});}
    else {let d=new Date(start);while(iso(d)<='2026-10-10'){const end=new Date(d);end.setUTCDate(end.getUTCDate()+(7-end.getUTCDay())%7);if(iso(end)>'2026-10-10')end.setTime(Date.parse('2026-10-10T00:00:00Z'));const next=new Date(end);next.setUTCDate(next.getUTCDate()+1);trend.push({start_date:iso(d),end_date:iso(end),start_utc:d.toISOString(),end_utc:next.toISOString(),count:0});d=next;}}
    const total=mode==='empty'?0:80;if(total)trend.forEach((b,i)=>b.count=i===0?total:0);
    const ratings=mode==='normal'?48:0;
    return {scope:{namespace,days,timezone,local_start_date:iso(start),local_end_date:'2026-10-10',window_start_utc:start.toISOString(),window_end_utc:'2026-10-10T07:32:00Z',available_from:null,coverage_complete:null},
      search:{total_searches:total,successful_searches:total?78:0,failed_searches:total?2:0,non_empty_searches:total?70:0,modes:[{mode:'semantic',count:total?40:0},{mode:'lexical',count:total?22:0},{mode:'anchor',count:total?10:0},{mode:'meta',count:total?8:0}],trend},
      feedback:{eligible_searches:total?70:0,rated_searches:ratings?24:0,coverage_percent:total?(ratings?34.29:0):null,rating_count:ratings,average_rating:ratings?rating:null,distribution:{positive:ratings?20:0,neutral:ratings?8:0,negative:ratings?20:0}},
      data_quality:{skipped_malformed_lines:3,skipped_unrecognized_source:0,excluded_test_reports:7,unmatched_reports:2,conflicting_search_ids:0,warnings:['audit_changed_during_scan']}};
  };
  S.setOverride(url=>{
    if(!url.startsWith('/api/stats/retrieval'))return undefined;
    const params=new URL(url,location.origin).searchParams;
    if(hold){const callback=hold;hold=null;return new Promise(resolve=>callback(()=>resolve(json(fixture(params)))));}
    if(failed)return json({detail:'<img src=x onerror=window.retrievalInjected=1> read failed'},500);
    if(params.get('namespace')==='missing')return json({detail:'unknown namespace',response_code:'namespace_not_found'},400);
    const f=fixture(params);if(mode==='invalid')f.search.total_searches=999;return json(f);
  });
  const ready=()=>waitFor(()=>$('stats-content').getAttribute('aria-busy')==='false'&&$('stats-retrieval-overview'),'Retrieval ready');
  const requests=()=>S.calls.filter(c=>c.url.startsWith('/api/stats/retrieval'));
  $('btn-refresh').click();await ready();
  const metricIcons=[...$('stats-retrieval-overview').querySelectorAll('.stats-metric-icon')];
  assert(metricIcons.length===3&&metricIcons.every(e=>e.querySelector('svg[focusable=false]'))&&new Set(metricIcons.map(e=>getComputedStyle(e).color)).size===3,'three distinct decorative Retrieval metric icons');
  assert(getComputedStyle($('stats-search-modes').querySelector('.stats-retrieval-bar')).height==='8px','mode bar weight matches mockup');
  const modeCard=$('stats-search-modes');
  if(modeCard.clientWidth-2*parseFloat(getComputedStyle(modeCard).paddingLeft)>=360)assert(getComputedStyle(modeCard.querySelector('.stats-retrieval-bar')).gridRowStart==='1','wide mode bars share text row');
  assert($('stats-tab-retrieval').textContent==='LLM Retrieval'&&$('stats-retrieval-source').textContent==='MCP search activity and reported feedback','Retrieval identifies LLM and MCP source');
  assert(!document.getElementById('btn-stats-refresh'),'no local Refresh');
  assert($('stats-search-modes').querySelectorAll('.stats-retrieval-row').length===4,'four modes');
  assert(new Set([...$('stats-search-modes').querySelectorAll('.stats-retrieval-bar>span')].map(e=>getComputedStyle(e).backgroundColor)).size===4,'four distinct mode identity colors');
  assert(!!$('stats-data-details')===(document.documentElement.dataset.uiDebug==='true'),'Data notes are startup-Debug-only');
  assert(JSON.stringify(store.getNamespaces())===candidates&&JSON.stringify(store.getCategories())===categories,'global candidates intact');
  if(scenario!=='retrieval-functional'&&scenario!=='retrieval-debug-disabled'&&scenario!=='retrieval-reload') {
    const days=scenario.includes('-90-')?90:scenario.includes('-30-')?30:7;
    $('stats-days-'+days).click();await ready();
    assert(document.documentElement.scrollWidth<=innerWidth,'no horizontal overflow');
    assert([...document.querySelectorAll('#stats-content .stats-panel')].every(p=>p.getBoundingClientRect().right<=innerWidth),'panels fit');
    const selectedGeom=JSON.stringify(geometry());$('stats-tab-memory').click();await waitFor(()=>$('stats-overview'),'Memory return');assert(JSON.stringify(geometry())===selectedGeom,'switch does not move');
    $('stats-tab-retrieval').click();await ready();assert(JSON.stringify(geometry())===selectedGeom,'switch position restored');
    const theme=await import('/admin/console/static/js/theme.js'),oldTheme=document.documentElement.dataset.themePreference;
    const bounds=()=>JSON.stringify([...document.querySelectorAll('.stats-view-controls,#stats-content .stats-panel')].map(p=>{const r=p.getBoundingClientRect();return [r.x,r.y,r.width,r.height];}));
    const beforeTheme=bounds();theme.setTheme(oldTheme==='dark'?'light':'dark');await settle();assert(bounds()===beforeTheme,'theme geometry identical');theme.setTheme(oldTheme);
    result.textContent='PASS: '+scenario;return true;
  }
  assert($('stats-feedback-note-icon').matches('svg[aria-hidden=true][focusable=false]'),'footer explanation has decorative information icon');
  assert(!$('stats-feedback').querySelector('.stats-feedback-summary').textContent.includes('Valid Ratings')&&$('stats-feedback-note').textContent.endsWith('Valid ratings: 48.'),'valid rating count lives in footer explanation');
  assert($('stats-feedback').textContent.includes('-2.5')&&$('stats-feedback').textContent.includes('−10 to +10'),'numeric negative rating scale');
  const negativeFills=[...$('stats-average-rating-stars').children].map(e=>Number(e.dataset.fill));
  assert(negativeFills.length===10&&negativeFills.slice(0,7).every(n=>n===0)&&negativeFills[7]===.5&&negativeFills.slice(8).every(n=>n===1),'negative rating fills from the right');
  assert(getComputedStyle($('stats-average-rating-stars').children[7].querySelector('.stats-rating-star-fill')).color===getComputedStyle(document.querySelector('.stats-feedback-negative .stats-retrieval-bar>span')).backgroundColor,'negative stars use semantic red');
  assert(getComputedStyle($('stats-average-rating-value')).fontSize==='28px'&&getComputedStyle($('stats-average-rating-stars').firstChild).width==='18px','rating typography hierarchy');
  rating=7.57;$('btn-refresh').click();await ready();
  const fills=[...$('stats-average-rating-stars').children].map(e=>Number(e.dataset.fill));
  assert($('stats-average-rating-value').textContent==='7.57'&&fills.slice(0,7).every(n=>n===1)&&Math.abs(fills[7]-.57)<1e-9&&fills.slice(8).every(n=>n===0),'7.57 renders seven full stars and fractional eighth');
  rating=-7.57;$('btn-refresh').click();await ready();
  const negativeFraction=$('stats-average-rating-stars').children[2].querySelector('.stats-rating-star-fill');
  assert($('stats-average-rating-value').textContent==='-7.57'&&Math.abs(Number($('stats-average-rating-stars').children[2].dataset.fill)-.57)<1e-9&&[...$('stats-average-rating-stars').children].slice(3).every(e=>Number(e.dataset.fill)===1)&&Math.abs(negativeFraction.getBoundingClientRect().right-negativeFraction.parentElement.getBoundingClientRect().right)<.1&&Math.abs(negativeFraction.getBoundingClientRect().width/negativeFraction.parentElement.getBoundingClientRect().width-.57)<.002&&Math.abs(negativeFraction.querySelector('svg').getBoundingClientRect().right-negativeFraction.getBoundingClientRect().right)<.1,'negative fractional fill is anchored at the right');
  rating=-10;$('btn-refresh').click();await ready();assert([...$('stats-average-rating-stars').children].every(e=>Number(e.dataset.fill)===1)&&$('stats-average-rating-stars').dataset.direction==='negative','negative maximum fills ten red stars');
  rating=10;$('btn-refresh').click();await ready();assert([...$('stats-average-rating-stars').children].every(e=>Number(e.dataset.fill)===1),'ten maximum stars');
  rating=0;$('btn-refresh').click();await ready();assert($('stats-average-rating-value').textContent==='0'&&[...$('stats-average-rating-stars').children].every(e=>Number(e.dataset.fill)===0),'zero is numeric with empty stars');
  rating=-2.5;$('btn-refresh').click();await ready();
  assert($('stats-search-modes').querySelector('.stats-percent').textContent==='50.0%','mode uses all calls');
  assert($('stats-feedback').querySelector('.stats-feedback-positive .stats-percent').textContent==='41.7%','feedback uses rating count');
  const barColor=selector=>getComputedStyle(document.querySelector(selector+' .stats-retrieval-bar>span')).backgroundColor;
  assert(new Set(['.stats-feedback-positive','.stats-feedback-neutral','.stats-feedback-negative'].map(barColor)).size===3,'distinct feedback semantic colors');
  const accentProbe=document.createElement('span');accentProbe.style.color='var(--accent)';$('stats-feedback').append(accentProbe);
  assert(barColor('.stats-feedback-neutral')===getComputedStyle(accentProbe).color,'neutral feedback matches blue accent');accentProbe.remove();
  assert($('stats-search-activity').querySelectorAll('table tr').length===8,'all seven daily buckets retained');
  let chart=document.querySelector('.stats-trend-svg');chart.focus();assert(!document.querySelector('.stats-trend-tooltip').hidden,'chart focus tooltip');
  chart.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}));assert(document.querySelector('.stats-trend-tooltip').textContent.includes('2026-10-10'),'chart last bucket');
  chart.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert(document.querySelector('.stats-trend-tooltip').hidden,'chart Escape');
  chart.dispatchEvent(new PointerEvent('pointerdown',{clientX:chart.getBoundingClientRect().left+50,pointerType:'touch',bubbles:true}));assert(!document.querySelector('.stats-trend-tooltip').hidden,'touch tooltip');
  $('stats-days-30').click();await ready();assert(document.querySelectorAll('.stats-trend table tr').length===31,'30 buckets');
  $('stats-namespace').value=ns;fire($('stats-namespace'),'change');await ready();
  $('stats-tab-memory').click();await waitFor(()=>$('stats-overview'),'Memory selection');$('stats-keywords-top-100').click();$('stats-keywords-ranking').scrollTop=100;
  const memoryScroll=$('stats-keywords-ranking').scrollTop;
  $('stats-tab-retrieval').click();await ready();assert($('stats-namespace').value===ns&&$('stats-days-30').getAttribute('aria-pressed')==='true','shared scope and view-local days');
  $('stats-tab-memory').click();await waitFor(()=>$('stats-overview'),'Memory preferences return');
  assert($('stats-keywords-top-100').getAttribute('aria-pressed')==='true'&&$('stats-keywords-ranking').scrollTop===memoryScroll,'Memory Top and list scroll survive view switch');
  $('stats-tab-retrieval').click();await ready();
  if(scenario==='retrieval-reload'){sessionStorage.setItem('retrieval-reload-stage','verify');location.reload();return true;}
  S.resetCalls();$('btn-refresh').click();await ready();assert(requests().length===1,'toolbar sends one active retrieval request');
  const params=new URL(requests()[0].url,location.origin).searchParams;assert(params.get('days')==='30'&&params.get('namespace')===ns&&params.get('timezone')===Intl.DateTimeFormat().resolvedOptions().timeZone,'actual request parameters');
  $('content').scrollTop=90;const scroll=$('content').scrollTop;router.switchPage('memories');router.switchPage('stats');await ready();assert($('content').scrollTop===scroll&&$('stats-tab-retrieval').getAttribute('aria-selected')==='true','navigation restores retrieval: expected '+scroll+', actual '+$('content').scrollTop+', selected '+$('stats-tab-retrieval').getAttribute('aria-selected'));
  let release;hold=callback=>release=callback;$('stats-days-7').click();await waitFor(()=>release,'held request');$('stats-days-90').click();await ready();release();await settle();assert($('stats-days-90').getAttribute('aria-pressed')==='true'&&document.querySelector('.stats-trend table').textContent.includes('2026-07-13'),'stale daily response ignored');
  mode='empty';$('btn-refresh').click();await ready();assert($('stats-retrieval-overview').textContent.includes('—')&&document.querySelector('.stats-trend-empty')&&!document.querySelector('.stats-trend-svg'),'empty search');
  mode='nofeedback';$('btn-refresh').click();await ready();assert($('stats-retrieval-overview').textContent.includes('0.0%')&&$('stats-feedback').textContent.includes('No feedback')&&!document.querySelector('.stats-feedback-positive'),'no feedback differs from undefined coverage');
  assert($('stats-feedback-note').textContent.endsWith('Valid ratings: 0.'),'footer rating count handles zero');
  assert($('stats-average-rating-value').textContent==='—'&&$('stats-average-rating-stars').hidden&&getComputedStyle($('stats-average-rating-stars')).display==='none','missing rating shows dash without stars');
  failed=true;$('btn-refresh').click();await waitFor(()=>$('stats-content').querySelector('[role=alert]'),'read error');assert(!$('stats-content').querySelector('img'),'safe error');failed=false;mode='normal';$('stats-content').querySelector('button').click();await ready();
  mode='invalid';$('btn-refresh').click();await waitFor(()=>$('stats-content').textContent.includes('Invalid retrieval'),'contract failure');mode='normal';$('stats-content').querySelector('button').click();await ready();
  $('stats-namespace').append(new Option('missing','missing'));$('stats-namespace').value='missing';fire($('stats-namespace'),'change');await waitFor(()=>$('stats-content').querySelector('[role=alert]'),'unknown namespace');[...$('stats-content').querySelectorAll('button')].find(b=>b.textContent==='All Namespaces').click();await ready();
  assert(!$('stats-data-details')||$('stats-data-details').textContent.includes('Global retained-audit'),'diagnostic scope');
  result.textContent='PASS: '+scenario;return true;
}
