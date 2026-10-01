// router.js — page switching with explicit per-page show hooks.

import { $ } from './core.js';

const pages = new Map();

export function register(id, { onShow }) {
  pages.set(id, { onShow });
}

export function switchPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav a').forEach(a => a.classList.remove('active'));
  $('page-' + name).classList.add('active');
  const navLink = document.querySelector(`.nav a[data-page="${name}"]`);
  if (navLink) navLink.classList.add('active');
  const page = pages.get(name);
  if (page && page.onShow) page.onShow();
}

export function bindNav() {
  document.querySelectorAll('.nav a[data-page]').forEach(a => {
    a.addEventListener('click', () => switchPage(a.dataset.page));
  });
}
