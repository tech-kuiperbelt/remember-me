// main.js — the single frontend entry point: mount modules, register pages and
// start maintenance polling. It is also the assembly point that wires
// cross-page navigation callbacks, so feature modules never import each other.

import { $ } from './core.js';
import { debugEnabled, toggleDebug } from './ui.js';
import { switchPage, register, bindNav, setLeaveGuard, startNavigation } from './router.js';
import * as detail from './detail.js';
import * as memories from './memories.js';
import * as stats from './stats.js';
import * as ops from './ops.js';
import * as audit from './audit.js';
import * as layout from './layout.js';
import * as theme from './theme.js';
import * as help from './help.js';
import * as versionUpdate from './version-update.js';

function refreshStats() {
  stats.loadStats();
  memories.loadMemories();
}

function mountAll() {
  bindNav();
  layout.mount();
  theme.mount();
  versionUpdate.mount({
    onConfigureProxy: () => switchPage('ops', () => {}, {focusProxy:true}),
    onHelp: () => switchPage('help', () => {}, {topic:'configuration',anchor:'help-updates'}),
  });
  help.mount({ onNavigate: (topic, options) => switchPage('help', () => {}, { ...options, topic }) });
  detail.mount({retryCategories: stats.loadStats});
  setLeaveGuard(detail.requestLeave);
  memories.mount({
    openConfiguration: () => switchPage('ops'),
    openMemory: detail.openMemory, openCreate: detail.openCreate, isDetailOpen: detail.isOpen, onDetailSelection: detail.onSelectionChanged, retryCategories: stats.loadStats });
  stats.mount({onDrilldown: (dimension, value, namespace) => {
    switchPage('memories', () => memories.prepareMetadataDrilldown(dimension, value, namespace));
  }});
  ops.mount();
  $('nav-audit').hidden = !debugEnabled();
  if (debugEnabled()) audit.mount();
  $('btn-refresh').addEventListener('click', refreshStats);
  $('btn-debug').hidden = !debugEnabled();
  if (debugEnabled()) $('btn-debug').addEventListener('click', toggleDebug);
}

function registerPages() {
  register('memories', { onShow: memories.loadMemories, onHide: memories.hide });
  register('stats', { onShow: stats.show, onHide: stats.hide });
  register('ops', { onShow: ops.loadOpsPage, onHide: ops.hide });
  register('help', { onShow: help.show, normalizeTopic: help.normalizeTopic });
  if (debugEnabled()) register('audit', { onShow: audit.loadAudit, onHide: audit.hide });
}

(async () => {
  mountAll();
  registerPages();
  const state = await ops.refreshMaintenance();
  startNavigation({ forcePage: state?.required_reason === 'model_not_ready' ? 'ops' : undefined });
  if (state?.required_reason !== 'model_not_ready') {
    stats.loadStats();
    ops.fetchStoragePaths();
  }
})();

setInterval(() => { ops.refreshMaintenance(); }, 2000);
