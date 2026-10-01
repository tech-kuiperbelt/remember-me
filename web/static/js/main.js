// main.js — the single frontend entry point: mount modules, register pages and
// start maintenance polling. It is also the assembly point that wires
// cross-page navigation callbacks, so feature modules never import each other.

import { $ } from './core.js';
import { toggleDebug } from './ui.js';
import { switchPage, register, bindNav } from './router.js';
import * as detail from './detail.js';
import * as memories from './memories.js';
import * as search from './search.js';
import * as keywords from './keywords.js';
import * as add from './add.js';
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
  detail.mount();
  memories.mount({ preselectClearNamespace: ops.preselectClearNamespace });
  search.mount();
  keywords.mount({ showKeyword: search.showKeyword });
  add.mount();
  stats.mount();
  ops.mount();
  audit.mount();
  clear.mount();
  $('btn-refresh').addEventListener('click', refreshStats);
  $('btn-debug').addEventListener('click', toggleDebug);
}

function registerPages() {
  register('memories', { onShow: memories.loadMemories });
  register('search', { onShow: () => {} });
  register('add', { onShow: () => {} });
  register('keywords', { onShow: () => keywords.loadKeywords(true) });
  register('stats', { onShow: stats.loadStats });
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
