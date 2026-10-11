// main.js — the single frontend entry point: mount modules, register pages and
// start maintenance polling. It is also the assembly point that wires
// cross-page navigation callbacks, so feature modules never import each other.

import { $ } from './core.js';
import { debugEnabled, toggleDebug } from './ui.js';
import { switchPage, register, bindNav, setLeaveGuard } from './router.js';
import * as detail from './detail.js';
import * as memories from './memories.js';
import * as stats from './stats.js';
import * as ops from './ops.js';
import * as audit from './audit.js';
import * as clear from './clear.js';
import * as layout from './layout.js';
import * as theme from './theme.js';

function refreshStats() {
  stats.loadStats();
  memories.loadMemories();
}

function mountAll() {
  bindNav();
  layout.mount();
  theme.mount();
  detail.mount({retryCategories: stats.loadStats});
  setLeaveGuard(detail.requestLeave);
  memories.mount({
    openMemory: detail.openMemory, openCreate: detail.openCreate, isDetailOpen: detail.isOpen, onDetailSelection: detail.onSelectionChanged, retryCategories: stats.loadStats });
  stats.mount({onDrilldown: (dimension, value, namespace) => {
    switchPage('memories', () => memories.prepareMetadataDrilldown(dimension, value, namespace));
  }});
  ops.mount();
  audit.mount();
  clear.mount();
  $('btn-refresh').addEventListener('click', refreshStats);
  $('btn-debug').hidden = !debugEnabled();
  if (debugEnabled()) $('btn-debug').addEventListener('click', toggleDebug);
}

function registerPages() {
  register('memories', { onShow: memories.loadMemories, onHide: memories.hide });
  register('stats', { onShow: stats.show, onHide: stats.hide });
  register('ops', { onShow: ops.loadOpsPage });
  register('audit', { onShow: audit.loadAudit });
}

(async () => {
  mountAll();
  registerPages();
  const state = await ops.refreshMaintenance();
  if (state?.required_reason === 'model_not_ready') switchPage('ops');
  else {
    memories.loadMemories();
    stats.loadStats();
    ops.fetchStoragePaths();
  }
})();

setInterval(() => { ops.refreshMaintenance(); }, 2000);
