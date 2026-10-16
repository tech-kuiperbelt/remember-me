import { $ } from './core.js';
import { api } from './api.js';
import { t } from './i18n.js';

export function createConfiguration({available, refresh, changed, retryModel}) {
  let baseline=null, saving=false, loading=null;
  const restartKeys=new Set();
  function draft() {
    const proxy={enabled:$('model-proxy-enabled').checked};
    const url=$('model-proxy-url').value.trim();
    if(proxy.enabled && url)proxy.url=url;
    return {port:Number($('config-port').value),language:$('config-language').value,local_only:$('config-local-only').checked,
      debug:$('config-debug').checked,auto_download_model:$('model-auto-download').checked,proxy};
  }
  function error() {
    const d=draft();
    if(!Number.isInteger(d.port)||d.port<1||d.port>65535)return t('ops.config.error.port');
    if(!['zh-CN','en'].includes(d.language))return t('ops.config.error.language');
    if(d.proxy.enabled && !d.proxy.url && !baseline?.proxy?.url)return t('ops.config.error.proxyRequired');
    if(d.proxy.url) {
      try {
        const u=new URL(d.proxy.url);
        if(!/^https?:\/\//i.test(d.proxy.url)||!['http:','https:'].includes(u.protocol)||!u.hostname||u.port==='0'||!['','/'].includes(u.pathname)||u.search||u.hash||/\s/.test(d.proxy.url)||d.proxy.url.includes('***@'))throw new Error();
      }catch{return t('ops.config.error.proxyInvalid');}
    }
    return '';
  }
  function dirty() {
    if(!baseline)return false;
    const d=draft();
    return ['port','language','local_only','debug','auto_download_model'].some(k=>d[k]!==baseline[k]) || d.proxy.enabled!==baseline.proxy.enabled || !!d.proxy.url;
  }
  function update() {
    $('model-proxy-fields').hidden=!$('model-proxy-enabled').checked;
    $('config-local-only-warning').hidden=$('config-local-only').checked;
    $('config-validation').textContent=baseline ? error() : '';
    $('btn-model-save').disabled=!baseline||saving||!available()||!dirty()||!!error();
    $('ops-config-fields').disabled=!baseline||saving;
    $('config-load-retry').disabled=saving||!!loading;
  }
  function fill(config) {
    baseline=config;
    $('config-port').value=config.port; $('config-language').value=config.language;
    $('config-local-only').checked=config.local_only; $('config-debug').checked=config.debug;
    $('model-auto-download').checked=config.auto_download_model;
    $('model-proxy-enabled').checked=config.proxy.enabled;
    $('model-proxy-url').value='';
    $('model-proxy-saved').textContent=config.proxy.url ? t('ops.config.proxy.saved') : t('ops.config.proxy.empty');
    $('config-debug-refresh').hidden=(config.debug_effective ?? config.debug)===(document.documentElement.dataset.uiDebug==='true');
    // Language applies on the next page load, so offer a refresh (not a restart).
    $('config-language-refresh').hidden=config.language===(document.documentElement.lang||'zh-CN');
    $('config-load-retry').hidden=true;
    update();
  }
  async function load(force=false) {
    if(loading)return loading;
    if(baseline&&!force)return;
    loading=(async()=>{try { fill(await api('/config')); }
      catch(e){$('model-config-result').textContent=t('ops.config.loadFailed',{error:e.message});$('config-load-retry').hidden=false;}
      finally{loading=null;update();}})();
    return loading;
  }
  async function save() {
    if($('btn-model-save').disabled)return;
    const value=draft(), before=baseline;
    if(before.local_only && !value.local_only && !confirm(t('ops.config.confirmLan')))return;
    saving=true;update();changed();
    const result=$('model-config-result');result.classList.remove('is-error');
    try {
      await refresh();
      if(!available())throw new Error(t('ops.config.unavailable'));
      const saved=await api('/config',{method:'POST',body:JSON.stringify(value)});
      for(const k of ['port','local_only'])if(value[k]!==before[k])restartKeys.add(k);
      fill(saved);
      result.textContent=t('ops.config.saved');
      if(restartKeys.size){
        $('config-restart-text').textContent=t('ops.config.restartNeeded');
        $('config-restart-notice').hidden=false;
      }
      const state=await refresh();
      if(state?.required_reason==='model_not_ready') {
        try { await retryModel();result.textContent+=' '+t('ops.config.preparingModel'); }
        catch(e){result.textContent+=' '+t('ops.config.retryFailed',{error:e.message});result.classList.add('is-error');}
      }
    }catch(e){result.textContent=t('ops.config.saveFailed',{error:e.message});result.classList.add('is-error');}
    finally{saving=false;update();changed();await refresh();}
  }
  $('ops-config-fields').addEventListener('input',update);
  $('ops-config-fields').addEventListener('change',update);
  $('btn-model-save').onclick=save;
  $('config-load-retry').onclick=()=>load(true);
  $('config-language-refresh-btn').onclick=()=>{
    if(dirty()&&!confirm(t('ops.config.discardConfirm')))return;
    location.reload();
  };
  return {load,update,isSaving:()=>saving,config:()=>baseline,hasDraft:dirty};
}
