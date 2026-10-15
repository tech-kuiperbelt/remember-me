// Application page switching; Help alone exposes public deep links.
import { $ } from './core.js';

const pages = new Map();
const historyKey = 'rememberNavigation';
let leaveGuard = action => { action(); return true; };
let current = null, traversal = null;
export function setLeaveGuard(guard) { leaveGuard = guard; }
export function register(id, { onShow, onHide, normalizeTopic }) {
  pages.set(id, { onShow, onHide, normalizeTopic });
}
function route(page, topic = 'home') {
  if (page === 'add') page = 'memories';
  if (!pages.has(page) || !$('page-' + page)) return null;
  return { page, topic: pages.get(page).normalizeTopic?.(topic) || 'home' };
}
function fromURL() {
  if (/^#help(?:\/|$)/.test(location.hash)) return route('help', location.hash.slice(6) || 'home');
  return route('memories');
}
function routeURL(target) {
  const url = new URL(location.href);
  url.hash = target.page === 'help' ? (target.topic === 'home' ? 'help' : 'help/' + target.topic) : '';
  return url;
}
function write(method, value) {
  history[method]({ ...history.state, [historyKey]: value }, '', routeURL(value));
}
function snapshot() {
  if (!current) return;
  current = { ...current, scroll: $('content').scrollTop,
    focus: ($('page-' + current.page).contains(document.activeElement) ? document.activeElement.id : null) };
  write('replaceState', current);
}
export function switchPage(name, prepare = () => {}, options = {}) {
  const target = route(name, options.topic);
  if (!target || traversal) return false;
  return leaveGuard(() => {
    // Save the old view before changing either the DOM or the URL.
    snapshot();
    prepare();
    if (current) {
      const same = current.page === target.page && current.topic === target.topic;
      current = { ...target, index: current.index + (same ? 0 : 1), scroll: 0, focus: null };
      write(same ? 'replaceState' : 'pushState', current);
    }
    showPage(target, options);
  });
}
function showPage(target, options = {}) {
  const previous = document.querySelector('.page.active')?.id.replace('page-', '');
  if (previous && previous !== target.page) pages.get(previous)?.onHide?.();
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav button[data-page]').forEach(btn => {
    btn.classList.remove('active'); btn.removeAttribute('aria-current');
  });
  $('page-' + target.page).classList.add('active');
  const nav = document.querySelector(`.nav button[data-page="${target.page}"]`);
  if (nav) { nav.classList.add('active'); nav.setAttribute('aria-current', 'page'); }
  $('content').scrollTop = 0;
  pages.get(target.page)?.onShow?.(target.topic, options);
  if (options.restore) {
    const focus = target.focus && $(target.focus);
    if (focus?.getClientRects().length) focus.focus({ preventScroll: true });
    $('content').scrollTop = target.scroll || 0;
  }
}

// Restore the displayed entry before asking the asynchronous editor leave guard.
// Cancelling leaves URL, history position and draft untouched; acceptance replays
// exactly the requested traversal without creating a new history entry.
function onPopState(event) {
  if (traversal?.phase === 'restore') {
    const request = traversal;
    traversal = null;
    write('replaceState', current);
    leaveGuard(() => {
      traversal = { ...request, phase: 'apply' };
      history.go(request.delta);
    });
    return;
  }
  if (traversal?.phase === 'apply') {
    current = traversal.target;
    traversal = null;
    write('replaceState', current);
    showPage(current, { restore: true });
    return;
  }
  if (!current) return;
  const saved = event.state?.[historyKey];
  const resolved = (saved && Number.isInteger(saved.index) ? route(saved.page, saved.topic) : null) || fromURL();
  if (!resolved) return;
  const target = { ...saved, ...resolved, index: saved?.index ?? current.index + 1 };
  const delta = target.index - current.index;
  // Capture the still-visible page without overwriting the destination entry.
  current = { ...current, scroll: $('content').scrollTop,
    focus: ($('page-' + current.page).contains(document.activeElement) ? document.activeElement.id : null) };
  if (!delta) {
    write('replaceState', current);
    leaveGuard(() => { current = target; write('replaceState', current); showPage(current, { restore: true }); });
    return;
  }
  traversal = { phase: 'restore', target, delta };
  history.go(-delta);
}
export function startNavigation({ forcePage } = {}) {
  const target = forcePage ? route(forcePage) : fromURL();
  const previousIndex = history.state?.[historyKey]?.index;
  current = { ...target, index: Number.isInteger(previousIndex) ? previousIndex : 0, scroll: 0, focus: null };
  write('replaceState', current);
  window.addEventListener('popstate', onPopState);
  showPage(current);
}
export function bindNav() {
  document.querySelectorAll('.nav button[data-page]').forEach(btn => {
    btn.addEventListener('click', () => switchPage(btn.dataset.page));
  });
}
