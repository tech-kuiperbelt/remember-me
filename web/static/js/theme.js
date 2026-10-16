// theme.js — runtime theme preference, system listener and appearance control.
// The tiny inline bootstrap in index.html resolves the first paint before CSS.

import { $ } from './core.js';

const STORAGE_KEY = 'remember-me.theme.v1';
const CHOICES = ['system', 'light', 'neutral', 'dark'];
const media = window.matchMedia('(prefers-color-scheme: dark)');

let preference = 'system';

function readStored() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return CHOICES.includes(value) ? value : 'system';
  } catch (e) {
    return 'system';
  }
}

function resolveTheme(choice) {
  return choice === 'system' ? (media.matches ? 'dark' : 'light') : choice;
}

function apply() {
  document.documentElement.dataset.theme = resolveTheme(preference);
  document.documentElement.dataset.themePreference = preference;
  const favicon = $('brand-favicon');
  if (favicon) favicon.setAttribute('href', `/admin/console/static/img/brand/favicon-${resolveTheme(preference)}.svg`);
  const select = $('theme-select');
  if (select) {
    select.value = preference;
    select.querySelectorAll('input[name=theme]').forEach(input => { input.checked = input.value === preference; });
  }
}

function onSystemChange() {
  if (preference === 'system') apply();
}

export function setTheme(choice) {
  preference = CHOICES.includes(choice) ? choice : 'system';
  try { localStorage.setItem(STORAGE_KEY, preference); } catch (e) {}
  apply();
}

export function mount() {
  preference = readStored();
  apply();
  const select = $('theme-select');
  if (select) select.addEventListener('change', event => setTheme(event.target.matches('input[name=theme]') ? event.target.value : select.value));
  if (typeof media.addEventListener === 'function') media.addEventListener('change', onSystemChange);
  else if (typeof media.addListener === 'function') media.addListener(onSystemChange);
}
