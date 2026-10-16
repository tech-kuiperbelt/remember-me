// Configuration and maintenance orchestration. Backend admission stays authoritative.
import { $, formatBytes } from './core.js';
import { api } from './api.js';
import { setMaintenance, emitMemoriesChanged, emitStatsChanged, onNamespacesChanged, getNamespaces, getNamespaceCounts } from './store.js';
import { deriveStatus, statusText, statusDetail } from './ops-state.js';
import { createConfiguration } from './ops-config.js';
import { confirmDestructive } from './clear.js';
import { t } from './i18n.js';

let status=deriveStatus(null), config, busy=null, job=null, power=null, pollVersion=0, reconnecting=false;
let namespaceCounts={}, maintenanceRequest=null, needsRegistry=false;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const gates={enter:'enter',exit:'exit',retry:'retryModel',sync:'sync',restart:'restartShutdown',shutdown:'restartShutdown'};
const controls={enter:'btn-maintenance-enter',exit:'btn-maintenance-exit',retry:'btn-model-retry',sync:'btn-sync',restart:'btn-restart',shutdown:'btn-shutdown',import:'btn-import',recover:'btn-recover',clear:'btn-clear',migrate:'btn-migrate',exportFull:'btn-export-full',exportNamespace:'btn-export-ns'};
const allowed=kind=>status[gates[kind]||'bulkActions'];
const locked=()=>!!busy||!!job||!!power||!!config?.isSaving();
function fileError(file,limit=false) {
  if(!file)return t('ops.error.selectFile');
  if(!/\.(jsonl|ndjson)$/i.test(file.name))return t('ops.error.fileType');
  if(limit&&file.size>64*1024*1024)return t('ops.error.fileTooLarge');
  return '';
}
function formError(kind) {
  if(kind==='import'||kind==='recover')return fileError($(kind+'-file').files[0],kind==='import');
  if(kind==='exportNamespace'&&!$('export-namespace').value)return t('ops.error.selectNamespace');
  if(kind==='migrate') {
    const source=$('migrate-source').value,target=$('migrate-target').value.trim();
    if(!source||!target)return t('ops.error.selectSourceTarget');
    if(source===target)return t('ops.error.sameSourceTarget');
    if(Array.from(target).length>64||['all','*','none','null'].includes(target.toLowerCase()))return t('ops.error.invalidTarget');
  }
  return '';
}
function applyControls() {
  for(const [kind,id] of Object.entries(controls)) {
    const reason=locked()?t('ops.error.busy'):!allowed(kind)?t('ops.error.actionUnavailable',{reason:statusDetail(status)}):formError(kind);
    $(id).disabled=!!reason;$(id).title=reason;
  }
  $('ops-action-reason').textContent=locked()?t('ops.actionReason.locked'):status.kind==='normal'?t('ops.actionReason.normal'):status.kind==='error'?t('ops.actionReason.error'):status.modelPending||status.kind==='unavailable'?statusDetail(status):'';
  config?.update();
}
function paint(next) {
  status=deriveStatus(next);
  if(status.modelPending)needsRegistry=true;
  if(needsRegistry&&!status.modelPending&&!status.hasError&&status.kind!=='unavailable'){needsRegistry=false;emitStatsChanged();}
  $('btn-status').dataset.state=status.kind;$('btn-status').textContent=statusText[status.kind];
  $('status-tooltip').textContent=statusDetail(status);
  $('maintenance-status').textContent=statusDetail(status);
  renderModelStatus(next?.model);
  setMaintenance(next);applyControls();
}
function message(id,text,error=false) { const el=$(id);el.textContent=text;el.classList.toggle('is-error',error); }
export function refreshMaintenance() {
  if(power)return Promise.resolve(null);
  if(maintenanceRequest)return maintenanceRequest;
  const version=++pollVersion;
  maintenanceRequest=(async()=>{
    try {
      const next=await api('/maintenance',{signal:AbortSignal.timeout(5000)});
      if(version!==pollVersion||power)return null;
      paint(next);return next;
    }catch(e) {
      if(version===pollVersion&&!power)paint({state:'unavailable',error:String(e.message)});
      return null;
    }finally{maintenanceRequest=null;}
  })();
  return maintenanceRequest;
}

function renderModelStatus(model) {
  const labels={waiting:t('model.waiting'),downloading:t('model.downloading'),validating:t('model.validating'),ready:t('model.ready'),failed:t('model.failed'),cancelling:t('model.cancelling')};
  $('model-status').textContent=model?(labels[model.status]||model.status):t('model.unavailable');
  $('model-progress-area').hidden=!model||!!model.ready;
  const bar=$('model-progress');
  if(model?.ready)bar.value=100;
  else if(Number.isFinite(model?.percent))bar.value=model.percent;
  else bar.removeAttribute('value');
  const percent=Number.isFinite(model?.percent)?' ('+model.percent.toFixed(1)+'%)':'';
  $('model-progress-detail').textContent=model?.total
    ? t('model.progress.detail',{file:model.file||t('model.progress.file'),completed:formatBytes(model.completed||0),total:formatBytes(model.total),percent})
    : t('model.progress.unknown');
  $('model-error').textContent=model?.error||'';
}
function updateNamespaces() {
  namespaceCounts=getNamespaceCounts();
  const names=getNamespaces().slice().sort();
  for(const [id,placeholder] of [['migrate-source',t('ops.export.namespace.placeholder')],['export-namespace',t('ops.export.namespace.placeholder')],['clear-ns',t('ops.clear.namespace.all')]]) {
    const select=$(id),previous=select.value;
    select.replaceChildren(new Option(placeholder,''),...names.map(name=>new Option(name+(id==='clear-ns'?'':` (${namespaceCounts[name]??0})`),name)));
    if(previous&&!names.includes(previous))select.add(new Option(previous,previous));
    select.value=previous;
  }
  $('migrate-target-suggestions').replaceChildren(...names.map(name=>new Option(name,name)));applyControls();
}
export async function loadOpsPage() {
  await refreshMaintenance();await config.load();updateNamespaces();
  if(!status.modelPending&&!status.hasError&&status.kind!=='unavailable'&&!power)emitStatsChanged();
  await fetchStoragePaths();
}
export function hide() {
  if(busy||job||power)return;
  for(const id of ['import-result','export-result','migrate-result','recover-result','clear-result','sync-result','maintenance-result','model-retry-result'])message(id,'');
}
function selectTab(name,focus=false) {
  for(const tab of ['settings','maintenance']) {
    $('ops-'+tab).hidden=tab!==name;
    $('ops-tab-'+tab).setAttribute('aria-selected',String(tab===name));$('ops-tab-'+tab).tabIndex=tab===name?0:-1;
  }
  if(focus)$('ops-tab-'+name).focus();
}
async function run(kind,result,operation,confirmation) {
  if(locked()||!allowed(kind)||formError(kind))return;
  busy=kind;applyControls();
  try {
    if(confirmation&&!await confirmation())return;
    await refreshMaintenance();
    if(!allowed(kind))throw new Error(t('ops.error.stateChanged'));
    message(result,t('ops.status.running'));
    await operation();
  }catch(e){message(result,t('ops.error.failed',{error:e.message}),true);}
  finally{busy=null;await refreshMaintenance();applyControls();}
}
function changedData() { emitMemoriesChanged();emitStatsChanged(); }
async function upload(path,file,prefix) {
  const form=new FormData();form.append('file',file);if(prefix!==undefined)form.append('namespace_prefix',prefix);
  const response=await fetch('/api/'+path,{method:'POST',body:form});
  const data=await response.json();if(!response.ok)throw new Error(data.detail||response.statusText);return data;
}
async function watchJob() {
  if(!job||job.polling)return;
  const current=job;current.polling=true;
  const result=current.kind+'-result', retry=$(current.kind+'-poll-retry');retry.hidden=true;
  try {
    while(job===current) {
      let data;
      try{data=await api('/'+current.kind+'/'+encodeURIComponent(current.id),{signal:AbortSignal.timeout(5000)});}
      catch(e){message(result,t('ops.job.uncertain',{id:current.id,error:e.message}),true);retry.hidden=false;return;}
      message(result,t('ops.job.progress',{id:current.id,phase:data.phase||data.status,progress:data.total!=null?' · '+data.completed+'/'+data.total:''}));
      if(data.status==='completed') {
        const r=data.result||{};
        message(result,current.kind==='import'?t('ops.import.result',{imported:r.imported??'—',skipped:r.skipped??'—',renamed:r.renamed??'—'}):t('ops.recover.result',{count:r.recovered??'—'}));
        job=null;changedData();return;
      }
      if(data.status==='failed') {job=null;throw new Error(data.error?.message||data.error||t('ops.job.failed'));}
      await sleep(750);
    }
  }catch(e){message(result,e.message,true);}
  finally{current.polling=false;await refreshMaintenance();applyControls();}
}
function importMemories() {
  const file=$('import-file').files[0],prefix=$('import-prefix').value.trim();
  return run('import','import-result',async()=>{
    const data=await upload('import',file,prefix);if(!data.import_id)throw new Error(t('ops.job.missingId'));
    job={kind:'import',id:data.import_id};await watchJob();
  });
}
function recover() {
  const file=$('recover-file').files[0];
  return run('recover','recover-result',async()=>{
    const data=await upload('recover',file);if(!data.id)throw new Error(t('ops.job.missingId'));
    job={kind:'recover',id:data.id};await watchJob();
  },()=>confirmDestructive({title:t('ops.recover.confirm.title'),scope:t('ops.recover.confirm.scope',{file:file.name}),action:t('ops.recover.confirm.action')}));
}
function clearMemories() {
  const ns=$('clear-ns').value;
  return run('clear','clear-result',async()=>{
    const data=await api('/memories'+(ns?'?namespace='+encodeURIComponent(ns):''),{method:'DELETE'});
    message('clear-result',t('ops.clear.result',{count:data.deleted}));changedData();
  },()=>confirmDestructive({title:t('ops.clear.confirm.title'),scope:ns?t('ops.clear.confirm.scope.ns',{ns}):t('ops.clear.confirm.scope.all'),action:t('ops.clear.confirm.action')}));
}
function migrate() {
  const source=$('migrate-source').value,target=$('migrate-target').value.trim(),count=namespaceCounts[source];
  return run('migrate','migrate-result',async()=>{
    const r=await api('/namespaces/migrate',{method:'POST',body:JSON.stringify({source,target})});
    if(r.error)throw new Error(r.error);
    message('migrate-result',t('ops.migrate.result',{count:r.migrated,source,target}));changedData();
  },()=>confirm(t('ops.migrate.confirm',{source,count:count==null?t('ops.migrate.confirm.all'):t('ops.migrate.confirm.count',{count}),target})));
}
async function downloadExport(path,options) {
  const response=await fetch('/api'+path,options);
  if(!response.ok){const error=await response.json();throw new Error(error.detail||response.statusText);}
  const blob=await response.blob(), disposition=response.headers.get('Content-Disposition')||'';
  const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i),plain=disposition.match(/filename="?([^";]+)"?/i);
  let name=plain?.[1]||'remember-me-export.jsonl';if(encoded){try{name=decodeURIComponent(encoded[1]);}catch{}}
  const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
  return response.headers.get('X-Memory-Count')||'—';
}
function exportMemories(full) {
  const ns=$('export-namespace').value;
  return run(full?'exportFull':'exportNamespace','export-result',async()=>{
    const count=await downloadExport(full?'/export/full':'/export/namespace',full?{method:'POST'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({namespace:ns})});
    message('export-result',t('ops.export.downloaded',{scope:full?t('ops.export.scope.full'):ns,count}));
  });
}
function sync() {return run('sync','sync-result',async()=>{const r=await api('/sync',{method:'POST'});if(r.error)throw new Error(r.error);message('sync-result',t('ops.sync.result',{count:r.synced??'—',errors:r.errors??'—'}));emitStatsChanged();});}
function maintenance(enter) {return run(enter?'enter':'exit','maintenance-result',async()=>{await api('/maintenance/'+(enter?'enter':'exit'),{method:'POST'});message('maintenance-result',enter?t('ops.maintenance.entered'):t('ops.maintenance.exited'));});}
function retryModel(){return run('retry','model-retry-result',async()=>{await api('/model/retry',{method:'POST'});message('model-retry-result',t('ops.model.requested'));});}

function addressHint() {
  const c=config.config();if(!c)return;
  const target=new URL(location.href);target.port=String(c.port);
  $('restart-address').textContent=t('ops.restart.address',{origin:target.origin,localOnly:c.local_only?t('ops.restart.address.localOnly'):''});
}
async function reconnect() {
  if(reconnecting||power!=='restart')return;
  reconnecting=true;$('restart-poll-retry').hidden=true;
  const deadline=Date.now()+60000;
  try {
    while(Date.now()<deadline) {
      await sleep(Math.min(2000,Math.max(0,deadline-Date.now())));
      if(Date.now()>=deadline)break;
      try {
        const snapshot=await api('/maintenance',{signal:AbortSignal.timeout(Math.min(5000,Math.max(1,deadline-Date.now())))});
        if(!['normal','maintenance'].includes(snapshot.state)||snapshot.action)continue;
        power=null;paint(snapshot);await config.load(true);
        message('restart-result',t('ops.restart.reconnected'));return;
      }catch{}
    }
    message('restart-result',t('ops.restart.timeout'),true);
    $('restart-poll-retry').hidden=false;
  }finally{reconnecting=false;applyControls();}
}
function controlServer(restart) {
  return run(restart?'restart':'shutdown','restart-result',async()=>{
    try{await api(restart?'/restart':'/shutdown',{method:'POST'});}
    catch(e){throw new Error(t('ops.error.resultUnknown',{error:e.message}));}
    power=restart?'restart':'shutdown';++pollVersion;
    paint({state:'unavailable'});addressHint();
    message('restart-result',restart?t('ops.restart.accepted'):t('ops.shutdown.accepted'));
    if(restart)void reconnect();
  },()=>confirm(restart?t('ops.restart.confirm'):t('ops.shutdown.confirm')));
}
export async function fetchStoragePaths() {
  if(status.modelPending||status.kind==='unavailable')return;
  try {const data=await api('/storage-paths');$('sync-paths').textContent=data.backend==='dual'?`${data.primary_path} → ${data.secondary_path}`:t('ops.sync.paths.missing');}catch{}
}
function cardIcons() {
  const paths=['M3 4h18v12H3z M8 21h8 M12 16v5','M12 3v3 M12 18v3 M3 12h3 M18 12h3 M8 8h8v8H8z','M4 5h16v14H4z M8 9h8 M8 13h5','M8 3h8v4H8z M5 7h14v14H5z M9 11h6 M9 15h6','M12 3v12 M7 8l5-5 5 5 M4 15v6h16v-6','M4 5h16v14H4z M8 9h8 M8 13h8','M3 3h18v7H3z M3 14h18v7H3z M7 6h.01 M7 17h.01','M12 3 2 21h20L12 3z M12 9v5 M12 17h.01'];
  document.querySelectorAll('#page-ops .ops-card-heading').forEach((heading,i)=>{
    const id=heading.parentElement.id;heading.id=id+'-heading';heading.querySelector('h3').id=id+'-title';heading.querySelector('p').id=id+'-description';
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    for(const [k,v] of Object.entries({viewBox:'0 0 24 24',fill:'none','stroke-width':1.6,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':true}))svg.setAttribute(k,v);
    svg.id=id+'-icon';
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[i]);svg.append(path);heading.prepend(svg);
  });
}
export function mount() {
  config=createConfiguration({available:()=>status.kind!=='unavailable'&&!busy&&!job&&!power,refresh:refreshMaintenance,changed:applyControls,retryModel:()=>api('/model/retry',{method:'POST'})});
  cardIcons();onNamespacesChanged(updateNamespaces);
  for(const name of ['settings','maintenance'])$('ops-tab-'+name).onclick=()=>selectTab(name);
  $('ops-tabs').onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();selectTab(e.key==='Home'?'settings':e.key==='End'?'maintenance':$('ops-settings').hidden?'settings':'maintenance',true);}};
  $('config-open-maintenance').onclick=()=>selectTab('maintenance',true);
  const actions={enter:()=>maintenance(true),exit:()=>maintenance(false),retry:retryModel,sync,restart:()=>controlServer(true),shutdown:()=>controlServer(false),import:importMemories,recover,clear:clearMemories,migrate,exportFull:()=>exportMemories(true),exportNamespace:()=>exportMemories(false)};
  for(const [kind,id] of Object.entries(controls))$(id).onclick=actions[kind];
  $('ops-maintenance').addEventListener('input',applyControls);$('ops-maintenance').addEventListener('change',applyControls);
  for(const kind of ['import','recover'])$(kind+'-poll-retry').onclick=()=>watchJob();
  $('restart-poll-retry').onclick=()=>reconnect();
  for(const details of document.querySelectorAll('#ops-maintenance details'))details.addEventListener('toggle',()=>{
    if(details.open)return;
    const kind=details.id.slice(4);
    if(busy===kind||(kind==='export'&&busy?.startsWith('export'))||job?.kind===kind){details.open=true;return;}
    if($(kind+'-result'))message(kind+'-result','');
  });
  applyControls();
}
