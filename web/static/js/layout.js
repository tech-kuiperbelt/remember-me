// layout.js — owns the desktop shell: sidebar collapse state, persistence and
// button attributes. No other module manages the collapsed state.

import { $ } from './core.js';

const STORAGE_KEY = 'remember-me.sidebar.collapsed.v1';

let collapsed = false;

function readStored() {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'true';
  } catch (e) {
    return null;  // storage unavailable (private mode, disabled): keep in-memory value
  }
}

function writeStored(value) {
  try {
    localStorage.setItem(STORAGE_KEY, value ? 'true' : 'false');
  } catch (e) {
    // ignore — in-memory state remains authoritative for this page
  }
}

function apply() {
  const shell = $('app-shell');
  const toggle = $('btn-sidebar-toggle');
  shell.classList.toggle('sidebar-collapsed', collapsed);
  toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  toggle.setAttribute('aria-label', collapsed ? '展开导航' : '收起导航');
  toggle.setAttribute('title', collapsed ? '展开导航' : '收起导航');
}

function toggle() {
  collapsed = !collapsed;
  writeStored(collapsed);
  apply();
}

export function mount() {
  const stored = readStored();
  if (stored !== null) collapsed = stored;
  apply();
  $('btn-sidebar-toggle').addEventListener('click', toggle);
}
