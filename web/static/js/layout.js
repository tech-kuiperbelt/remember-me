// layout.js — owns the desktop shell: sidebar collapse state and persistence.
// Blank-nav clicks and focused-nav keyboard activation toggle the sidebar.

import { $ } from './core.js';

const STORAGE_KEY = 'remember-me.sidebar.collapsed.v1';

let collapsed = false;
let tooltip;
let tooltipTarget;

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
  const nav = $('nav');
  shell.classList.toggle('sidebar-collapsed', collapsed);
  nav.setAttribute('aria-label', collapsed
    ? '主导航，已折叠；按 Enter 或点击导航空白处展开'
    : '主导航，已展开；按 Enter 或点击导航空白处折叠');
  if (!collapsed) hideNavTooltip();
}

function hideNavTooltip() {
  if (!tooltipTarget) return;
  if (tooltipTarget.getAttribute('aria-describedby') === 'nav-tooltip') {
    tooltipTarget.removeAttribute('aria-describedby');
  }
  tooltipTarget = null;
  tooltip.hidden = true;
}

function showNavTooltip(button) {
  if (!collapsed || !button.classList.contains('nav-item')) return;
  hideNavTooltip();
  const label = button.querySelector('.nav-label')?.textContent.trim();
  if (!label) return;
  tooltip.textContent = label;
  const rect = button.getBoundingClientRect();
  tooltip.hidden = false;
  tooltip.style.left = `${Math.round(rect.right + 8)}px`;
  tooltip.style.top = `${Math.round(rect.top + (rect.height - tooltip.offsetHeight) / 2)}px`;
  button.setAttribute('aria-describedby', 'nav-tooltip');
  tooltipTarget = button;
}

function toggle() {
  collapsed = !collapsed;
  writeStored(collapsed);
  apply();
}

export function mount() {
  tooltip = document.createElement('div');
  tooltip.id = 'nav-tooltip';
  tooltip.className = 'nav-tooltip';
  tooltip.setAttribute('role', 'tooltip');
  tooltip.hidden = true;
  document.body.append(tooltip);

  const stored = readStored();
  if (stored !== null) collapsed = stored;
  apply();
  const nav = $('nav');
  nav.addEventListener('click', event => {
    if (event.target.closest('.nav-item')) return;
    toggle();
  });
  nav.addEventListener('keydown', event => {
    if (event.target !== nav || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    toggle();
  });
  document.querySelectorAll('.nav button[data-page]').forEach(button => {
    const label = button.querySelector('.nav-label')?.textContent.trim();
    if (label) button.setAttribute('aria-label', label);
    button.addEventListener('mouseenter', () => showNavTooltip(button));
    button.addEventListener('mouseleave', hideNavTooltip);
    button.addEventListener('focus', () => showNavTooltip(button));
    button.addEventListener('blur', hideNavTooltip);
  });
  $('nav').addEventListener('scroll', hideNavTooltip, true);
  window.addEventListener('resize', hideNavTooltip);
}
