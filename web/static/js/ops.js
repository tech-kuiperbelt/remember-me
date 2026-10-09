// ops.js — service configuration, maintenance/model preparation, JSONL
// import/export/recover/sync/migrate and server process control.

import { $, formatBytes } from './core.js';
import { api } from './api.js';
import { setMaintenance, emitMemoriesChanged, emitStatsChanged } from './store.js';

let serviceConfigLoaded = false;
let configSaving = false;

export async function loadOpsPage() {
  const state = await refreshMaintenance();
  await loadModelConfig();
  if (state && state.required_reason === 'model_not_ready') return;
  const data = await api('/stats');
  const nsList = data.namespace_counts ? Object.keys(data.namespace_counts) : [];
  nsList.sort();
  // Source dropdown
  const srcSelect = $('migrate-source');
  srcSelect.innerHTML = '<option value="">Select namespace...</option>';
  nsList.forEach(ns => {
    const opt = document.createElement('option');
    opt.value = ns;
    opt.textContent = `${ns} (${data.namespace_counts[ns]})`;
    srcSelect.appendChild(opt);
  });
  // Target datalist suggestions
  const dl = $('migrate-target-suggestions');
  dl.innerHTML = '';
  nsList.forEach(ns => {
    const opt = document.createElement('option');
    opt.value = ns;
    opt.textContent = ns;
    dl.appendChild(opt);
  });
  const exportSel = $('export-namespace');
  const exportCur = exportSel.value;
  exportSel.innerHTML = '<option value="">Select namespace...</option>';
  nsList.forEach(ns => {
    const opt = document.createElement('option');
    opt.value = ns;
    opt.textContent = `${ns} (${data.namespace_counts[ns]})`;
    if (ns === exportCur) opt.selected = true;
    exportSel.appendChild(opt);
  });
  // Clear namespace dropdown
  const clrSel = $('clear-ns');
  const clrCur = clrSel.value;
  clrSel.innerHTML = '<option value="">(all namespaces)</option>';
  nsList.forEach(ns => {
    const opt = document.createElement('option');
    opt.value = ns;
    opt.textContent = ns;
    if (ns === clrCur) opt.selected = true;
    clrSel.appendChild(opt);
  });
  $('migrate-result').textContent = '';
}

// Derive the display status and control availability from a /api/maintenance
// snapshot. Priority: request failure/unparseable -> sticky action error ->
// model_not_ready -> maintenance -> normal. Gating mirrors the server authority
// in app/operations/maintenance.py (start_action / exit).
function deriveStatus(snapshot) {
  const s = snapshot || {};
  const idle = !s.action;
  const hasError = !!s.error;
  const modelPending = s.required_reason === 'model_not_ready' && !hasError;
  const isMaintenance = s.state === 'maintenance';
  let kind;
  if (hasError) kind = 'error';
  else if (modelPending) kind = 'model_not_ready';
  else if (isMaintenance) kind = 'maintenance';
  else if (s.state === 'normal') kind = 'normal';
  else kind = 'unavailable';

  const normal = kind === 'normal';
  const maintenanceReady = isMaintenance && idle && !hasError && !modelPending;
  // Mirrors maintenance.py start_action: required_reason allows only
  // restart/shutdown; a sticky error additionally allows sync; only a plain,
  // idle maintenance state allows the ordinary bulk actions.
  const processControl = (isMaintenance || modelPending) && idle;
  // sync is an ordinary maintenance action (and the repair path for a sticky
  // error); it is blocked while a model requirement is pending.
  const sync = isMaintenance && idle && !modelPending;

  return {
    kind, snapshot: s, modelPending, idle, hasError,
    enter: normal,
    exit: maintenanceReady,
    retryModel: modelPending && !['downloading', 'validating', 'cancelling'].includes(s.model?.status),
    restartShutdown: processControl,
    sync,
    bulkActions: maintenanceReady,
    normalWrites: normal,
  };
}

const STATUS_TEXT = {
  normal: '运行中',
  maintenance: '维护中',
  model_not_ready: '模型未就绪',
  error: '维护异常',
  unavailable: '状态不可用',
};

function statusDetail(status) {
  const s = status.snapshot || {};
  if (status.kind === 'unavailable') return status.message || '无法读取服务状态；请在下次轮询前保持谨慎。';
  const action = s.action ? ` · 当前动作：${s.action}` : '';
  if (status.kind === 'normal') return '服务正常运行。';
  if (status.kind === 'maintenance') return `写入暂停、查询可用${action}。`;
  if (status.kind === 'model_not_ready') {
    const model = s.model || {};
    const info = model.error || model.status || '准备中';
    return `记忆读写与搜索不可用；模型：${info}${action}。`;
  }
  return `维护操作失败：${s.error || '未知错误'} · state=${s.state || '-'}${action}。`;
}

function applyStatusChip(status) {
  const chip = $('btn-status');
  chip.dataset.state = status.kind;
  chip.textContent = STATUS_TEXT[status.kind];
  $('status-tooltip').textContent = statusDetail(status);
}

function applyControls(status) {
  $('btn-maintenance-enter').disabled = !status.enter;
  $('btn-maintenance-exit').disabled = !status.exit;
  $('btn-model-retry').disabled = !status.retryModel;
  $('btn-model-save').disabled = !serviceConfigLoaded || configSaving;
  $('btn-restart').disabled = !status.restartShutdown;
  $('btn-shutdown').disabled = !status.restartShutdown;
  $('btn-sync').disabled = !status.sync;
  for (const id of ['btn-recover', 'btn-import', 'btn-export-full', 'btn-export-ns', 'btn-clear', 'btn-migrate']) {
    $(id).disabled = !status.bulkActions;
  }
  $('maintenance-status').textContent = `State: ${status.snapshot.state || '-'}` +
    (status.snapshot.action ? ` · Running: ${status.snapshot.action}` : '') +
    (status.snapshot.error ? ` · Error: ${status.snapshot.error}` : '');
}

export async function refreshMaintenance() {
  try {
    const state = await api('/maintenance');
    const status = deriveStatus(state);
    applyStatusChip(status);
    applyControls(status);
    renderModelStatus(state.model);
    setMaintenance(state);
    return state;
  } catch (e) {
    // Safe snapshot: treat the service as unavailable so writers stay disabled
    // until a later successful poll replaces it.
    const status = deriveStatus({ state: 'unavailable' });
    status.message = 'Maintenance status unavailable: ' + e.message;
    applyStatusChip(status);
    applyControls(status);
    setMaintenance({ state: 'unavailable', error: String(e.message) });
  }
}

async function enterMaintenance() {
  $('maintenance-result').textContent = 'Waiting for active writes...';
  try {
    await api('/maintenance/enter', {method:'POST'});
    $('maintenance-result').textContent = 'Maintenance mode active.';
  } catch(e) { $('maintenance-result').textContent = 'Error: ' + e.message; }
  await refreshMaintenance();
}

async function exitMaintenance() {
  try {
    await api('/maintenance/exit', {method:'POST'});
    $('maintenance-result').textContent = 'Normal service restored.';
  } catch(e) { $('maintenance-result').textContent = 'Error: ' + e.message; }
  await refreshMaintenance();
}

function renderModelStatus(model) {
  if (!model) return;
  const labels = {waiting:'模型缓存缺失，请设置代理后重试', downloading:'正在下载 / 加载模型',
    validating:'正在验证模型与数据库', ready:'模型已就绪', failed:'模型准备失败，可修改代理后重试',
    cancelling:'正在结束上一次任务，随后使用新配置重试'};
  $('model-status').textContent = labels[model.status] || model.status;
  const bar = $('model-progress');
  if (model.ready) bar.value = 100;
  else if (model.percent != null) bar.value = model.percent;
  else bar.removeAttribute('value');
  $('model-progress-detail').textContent = model.total
    ? `${model.file || '当前文件 / 下载批次'}：${formatBytes(model.completed)} / ${formatBytes(model.total)} (${model.percent.toFixed(1)}%)`
    : '总量尚未确定；正在等待或准备模型。';
  if (model.ready) $('model-progress-detail').textContent = '模型加载与验证完成。';
  $('model-error').textContent = model.error || '';
}

function updateLocalOnlyWarning() {
  $('config-local-only-warning').hidden = $('config-local-only').checked;
}

async function loadModelConfig() {
  try {
    const config = await api('/config');
    $('config-port').value = config.port;
    $('config-language').value = config.language;
    $('config-local-only').checked = config.local_only;
    $('model-auto-download').checked = config.auto_download_model;
    $('model-proxy-enabled').checked = config.proxy.enabled;
    $('model-proxy-saved').textContent = config.proxy.url ? `已保存：${config.proxy.url}` : '尚未设置代理地址';
    $('model-proxy-url').value = '';
    updateLocalOnlyWarning();
    serviceConfigLoaded = true;
    $('btn-model-save').disabled = configSaving;
  } catch (e) {
    serviceConfigLoaded = false;
    $('btn-model-save').disabled = true;
    $('model-config-result').textContent = e.message;
  }
}

async function saveModelConfig() {
  const result = $('model-config-result');
  if (configSaving || !serviceConfigLoaded) return;
  configSaving = true;
  $('btn-model-save').disabled = true;
  try {
    const port = Number($('config-port').value);
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('端口必须是 1–65535 的整数');
    const proxy = {enabled: $('model-proxy-enabled').checked};
    const url = $('model-proxy-url').value.trim();
    if (url) proxy.url = url;
    await api('/config', {method:'POST', body:JSON.stringify({
      port, language:$('config-language').value,
      auto_download_model:$('model-auto-download').checked,
      local_only:$('config-local-only').checked, proxy})});
    result.textContent = '配置已保存；端口、语言与访问范围重启后生效。';
    const state = await refreshMaintenance();
    if (state?.required_reason === 'model_not_ready') {
      await api('/model/retry', {method:'POST'});
      result.textContent = '配置已保存，正在使用新配置准备模型；端口与语言重启后生效。';
    }
    await loadModelConfig();
  } catch (e) { result.textContent = '保存失败：' + e.message; }
  finally { configSaving = false; $('btn-model-save').disabled = !serviceConfigLoaded; }
  await refreshMaintenance();
}

async function retryModel() {
  try {
    await api('/model/retry', {method:'POST'});
    $('model-config-result').textContent = '已请求重试。';
  } catch (e) { $('model-config-result').textContent = e.message; }
  await refreshMaintenance();
}

async function doRecover() {
  const file = $('recover-file').files[0];
  const resultEl = $('recover-result');
  if (!file) { resultEl.textContent = 'Select a JSONL file.'; return; }
  if (!confirm(`Replace ALL current memories with records from ${file.name}?`)) return;
  const form = new FormData();
  form.append('file', file);
  resultEl.textContent = 'Uploading...';
  try {
    const response = await fetch('/api/recover', {method:'POST', body:form});
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || response.statusText);
    await refreshMaintenance();
    while (true) {
      const status = await api('/recover/' + encodeURIComponent(job.id));
      resultEl.textContent = `Recover: ${status.phase} ${status.completed}/${status.total}`;
      if (status.status === 'completed') {
        resultEl.textContent = `Recovered ${status.result.recovered} memories.`;
        emitMemoriesChanged();
        emitStatsChanged();
        break;
      }
      if (status.status === 'failed') throw new Error(status.error);
      await new Promise(resolve => setTimeout(resolve, 750));
    }
  } catch(e) { resultEl.textContent = 'Error: ' + e.message; }
  await refreshMaintenance();
}

async function doMigrate() {
  const source = $('migrate-source').value;
  const target = $('migrate-target').value.trim();
  if (!source || !target) {
    $('migrate-result').textContent = 'Please select source and enter target.';
    return;
  }
  if (!confirm(`Move ALL memories from "${source}" to "${target}"? This cannot be undone.`)) {
    return;
  }
  const btn = $('btn-migrate');
  const resultEl = $('migrate-result');
  btn.disabled = true;
  resultEl.textContent = 'Migrating...';
  try {
    const result = await api('/namespaces/migrate', {
      method: 'POST',
      body: JSON.stringify({source, target}),
    });
    if (result.error) {
      resultEl.textContent = result.error;
    } else {
      resultEl.textContent = `✅ Migrated ${result.migrated} memories from "${source}" to "${target}".`;
      loadOpsPage();
    }
  } catch(e) {
    resultEl.textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function doRestartServer() { return controlServer(true); }
async function doShutdownServer() { return controlServer(false); }

async function controlServer(restart) {
  const message = restart
    ? 'Restart the MCP server after active work finishes? Connections will need to reconnect.'
    : 'Shut down the MCP server after active work finishes? You will need to start it again from the terminal.';
  if (!confirm(message)) return;
  const resultEl = $('restart-result');
  const buttons = [$('btn-restart'), $('btn-shutdown')];
  buttons.forEach(btn => { btn.disabled = true; });
  resultEl.textContent = 'Sending request...';
  try {
    await api(restart ? '/restart' : '/shutdown', {method: 'POST'});
    resultEl.textContent = restart
      ? 'Restart accepted. Waiting for active work to finish; refresh once the server returns.'
      : 'Shutdown accepted. Waiting for active work to finish. Start the server from the terminal to use it again.';
  } catch(e) {
    resultEl.textContent = 'Request could not be confirmed: ' + e.message;
    buttons.forEach(btn => { btn.disabled = false; });
  }
}

async function doImport() {
  const file = $('import-file').files[0];
  const prefix = $('import-prefix').value.trim();
  const resultEl = $('import-result');
  const btn = $('btn-import');
  if (!file) { resultEl.textContent = 'Please select a JSONL file.'; return; }
  btn.disabled = true;
  resultEl.textContent = 'Importing...';
  try {
    const form = new FormData();
    form.append('file', file);
    form.append('namespace_prefix', prefix);
    const response = await fetch('/api/import', {method: 'POST', body: form});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || response.statusText);
    resultEl.textContent = `Import ${result.import_id}: queued`;
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 750));
      const status = await api('/import/' + encodeURIComponent(result.import_id));
      resultEl.textContent = `Import ${result.import_id}: ${status.phase}`;
      if (status.status === 'completed') {
        resultEl.textContent = `✅ Imported ${status.result.imported}; skipped ${status.result.skipped}; renamed ${status.result.renamed}.`;
        break;
      }
      if (status.status === 'failed') throw new Error(status.error.message);
    }
    emitMemoriesChanged();
    emitStatsChanged();
  } catch(e) {
    resultEl.textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function downloadExport(path, options) {
  const response = await fetch('/api' + path, options);
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || response.statusText);
  }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = match ? match[1] : 'remember-me-export.jsonl';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  return response.headers.get('X-Memory-Count') || '?';
}

async function doExportFull() {
  const resultEl = $('export-result');
  resultEl.textContent = 'Full Sync and export in progress...';
  try {
    const count = await downloadExport('/export/full', {method: 'POST'});
    resultEl.textContent = `✅ Downloaded full snapshot (${count} records).`;
  } catch (e) { resultEl.textContent = 'Error: ' + e.message; }
}

async function doExportNamespace() {
  const namespace = $('export-namespace').value;
  const resultEl = $('export-result');
  if (!namespace) { resultEl.textContent = 'Please select a namespace.'; return; }
  resultEl.textContent = 'Exporting namespace...';
  try {
    const count = await downloadExport('/export/namespace', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({namespace}),
    });
    resultEl.textContent = `✅ Downloaded ${namespace} (${count} records).`;
  } catch (e) { resultEl.textContent = 'Error: ' + e.message; }
}

async function doSync() {
  const resultEl = $('sync-result');
  const btn = $('btn-sync');
  btn.disabled = true;
  resultEl.textContent = 'Syncing...';
  try {
    const result = await api('/sync', {method: 'POST'});
    if (result.error) {
      resultEl.textContent = result.error;
    } else {
      resultEl.textContent = `✅ Synced ${result.synced || 0} records (errors: ${result.errors || 0}).`;
    }
  } catch(e) {
    resultEl.textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

export async function fetchStoragePaths() {
  try {
    const data = await api('/storage-paths');
    if (data.backend === 'dual' && data.primary_path && data.secondary_path) {
      const syncEl = $('sync-paths');
      if (syncEl) syncEl.textContent = `${data.primary_path} → ${data.secondary_path}`;
    }
  } catch(e) {
    // Not in dual mode — paths may be unavailable; leave placeholders empty.
  }
}

export function mount() {
  $('btn-model-save').addEventListener('click', saveModelConfig);
  $('config-local-only').addEventListener('change', updateLocalOnlyWarning);
  $('btn-model-retry').addEventListener('click', retryModel);
  $('btn-maintenance-enter').addEventListener('click', enterMaintenance);
  $('btn-maintenance-exit').addEventListener('click', exitMaintenance);
  $('btn-restart').addEventListener('click', doRestartServer);
  $('btn-shutdown').addEventListener('click', doShutdownServer);
  $('btn-recover').addEventListener('click', doRecover);
  $('btn-import').addEventListener('click', doImport);
  $('btn-export-full').addEventListener('click', doExportFull);
  $('btn-export-ns').addEventListener('click', doExportNamespace);
  $('btn-sync').addEventListener('click', doSync);
  $('btn-migrate').addEventListener('click', doMigrate);
}
