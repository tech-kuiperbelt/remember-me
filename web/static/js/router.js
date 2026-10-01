// router.js — page switching with explicit per-page show hooks.

import { $ } from './core.js';

const pages = new Map();

export function register(id, { onShow }) {
  pages.set(id, { onShow });
}

export function switchPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav button[data-page]').forEach(btn => {
    btn.classList.remove('active');
    btn.removeAttribute('aria-current');
  });
  $('page-' + name).classList.add('active');
  const navButton = document.querySelector(`.nav button[data-page="${name}"]`);
  if (navButton) {
    navButton.classList.add('active');
    navButton.setAttribute('aria-current', 'page');
  }
  const content = $('content');
  if (content) content.scrollTop = 0;
  const page = pages.get(name);
  if (page && page.onShow) page.onShow();
}

export function bindNav() {
  document.querySelectorAll('.nav button[data-page]').forEach(btn => {
    btn.addEventListener('click', () => switchPage(btn.dataset.page));
  });
}
