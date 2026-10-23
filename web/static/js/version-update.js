// Version presentation owns only local REST polling; GitHub access belongs to Runtime.
import { api } from './api.js';
import { t, locale } from './i18n.js';
import { copyText } from './markdown.js';

const STATUSES = new Set(['CHECKING', 'UP_TO_DATE', 'UPDATE_AVAILABLE', 'UPDATE_RECOMMENDED', 'UNAVAILABLE', 'NO_RELEASE']);
const RELEASES_URL = 'https://github.com/tech-kuiperbelt/remember-me/releases';
const CATEGORIES = ['upgrade', 'breaking', 'new', 'improvements', 'fixes'];
const narrow = matchMedia('(max-width: 640px)');
let snapshot = null, localError = false, posting = false, stopped = false;
let entry, panel, backdrop, tooltip, refreshButton, statusNode, body, stamp, cooldownNode;
let pollTimer, cooldownTimer, activeSince = 0, retryUntil = 0, readPending;
let sequence = 0, settledSequence = 0, postBarrier = 0;
let configureProxy = () => {}, openHelp = () => {};
const controllers = new Set();

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function icon(path) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '16'); svg.setAttribute('height', '16');
  svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.7'); svg.setAttribute('aria-hidden', 'true');
  const line = document.createElementNS(svg.namespaceURI, 'path');
  line.setAttribute('d', path); svg.append(line);
  return svg;
}
function button(key, path, onClick) {
  const node = element('button', 'version-icon-button');
  node.type = 'button'; node.setAttribute('aria-label', t(key)); node.title = t(key);
  node.append(icon(path)); node.addEventListener('click', onClick);
  return node;
}
function status() { return localError ? 'UNAVAILABLE' : snapshot?.check.status || 'CHECKING'; }
function checking() { return posting || (!localError && !!snapshot?.check.checking); }
function currentVersion() { return snapshot?.current.version ? `v${snapshot.current.version}` : 'Unknown'; }
function deadline() { return Math.max(retryUntil, Date.parse(snapshot?.check.next_allowed_at) || 0); }
function badgeText() {
  const state = status();
  if (state.startsWith('UPDATE_')) return 'Update';
  if (state === 'UNAVAILABLE') return 'Unavailable';
  if (state === 'NO_RELEASE') return 'No Release';
  return '';
}
function validSnapshot(data) {
  return data && data.current && data.check && STATUSES.has(data.check.status)
    && Number.isSafeInteger(data.check.revision) && data.check.revision >= 0
    && typeof data.check.checking === 'boolean'
    && (data.current.version === null || typeof data.current.version === 'string')
    && Array.isArray(data.releases) && Array.isArray(data.release_errors);
}
function accept(data, requestSequence) {
  if (!validSnapshot(data)) throw new Error('invalid local snapshot');
  if (requestSequence < postBarrier || (snapshot && data.check.revision < snapshot.check.revision)
      || (snapshot && data.check.revision === snapshot.check.revision && requestSequence < settledSequence)) return false;
  snapshot = data; settledSequence = Math.max(settledSequence, requestSequence); localError = false;
  if (data.check.checking && !activeSince) activeSince = Date.now();
  if (!data.check.checking) activeSince = 0;
  render(); return true;
}
function retryDeadline(value) {
  if (!value) return 0;
  const seconds = Number(value);
  return Number.isFinite(seconds) ? Date.now() + Math.max(0, seconds) * 1000 : Date.parse(value) || 0;
}
async function request(manual = false) {
  const requestSequence = ++sequence;
  if (manual) { posting = true; postBarrier = requestSequence; render(); }
  const controller = new AbortController(); controllers.add(controller);
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const data = await api('/version-update' + (manual ? '/check' : ''), {
      method: manual ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
      ...(manual ? {body: '{}'} : {}),
    });
    if (!stopped) accept(data, requestSequence);
  } catch (error) {
    if (stopped || requestSequence < postBarrier || requestSequence < settledSequence) return;
    if (error.status === 429) {
      try {
        if (accept(error.payload, requestSequence)) {
          retryUntil = Math.max(retryUntil, retryDeadline(error.retryAfter));
          updateRefresh();
        }
        return;
      } catch (_) { /* malformed local response is shown as a bounded service error */ }
    }
    settledSequence = requestSequence; localError = true; render();
  } finally {
    clearTimeout(timeout); controllers.delete(controller);
    if (manual) { posting = false; if (!stopped) render(); }
  }
}
function read() {
  if (readPending) return readPending;
  readPending = request().finally(() => { readPending = null; schedule(); });
  return readPending;
}
function schedule() {
  clearTimeout(pollTimer);
  if (stopped || document.hidden) return;
  // Short polling is bounded even if the local server never completes a check.
  const active = snapshot?.check.checking && activeSince && Date.now() - activeSince < 60000;
  pollTimer = setTimeout(read, active ? 1000 : 60000);
}
async function refresh() {
  if (checking() || deadline() > Date.now()) return;
  clearTimeout(pollTimer);
  await request(true);
  schedule();
}
function releaseLink(item, label = t('version.releaseLink')) {
  // Only canonical links supplied by the backend are accepted; never guess a tag URL.
  if (!item || !/^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$/.test(item.tag)
      || item.url !== `${RELEASES_URL}/tag/${item.tag}`) return null;
  const link = element('a', 'version-release-link', label);
  link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
  return link;
}
function explanation() {
  const state = status();
  if (localError) return t('version.localError');
  if (state === 'UNAVAILABLE') {
    const code = snapshot?.check.error_code || '';
    if (code.includes('rate_limited')) return t('version.error.rateLimit');
    if (code.includes('timeout')) return t('version.error.timeout');
    if (code.includes('tls_error')) return t('version.error.tls');
    if (code.includes('forbidden')) return t('version.error.forbidden');
    if (code.startsWith('latest_release_') || code.includes('invalid_response') || code.includes('too_large')) return t('version.error.release');
    return t('version.error.network');
  }
  if (state === 'UP_TO_DATE' && snapshot.current.version !== snapshot.latest?.version) return t('version.message.newer');
  return t('version.message.' + state);
}
function renderNotes() {
  if (localError || status() === 'UNAVAILABLE' || status() === 'NO_RELEASE' || !snapshot?.latest) return;
  if (snapshot.notes_scope === 'upgrade_range' && snapshot.notes_complete === false) {
    const notice = element('div', 'version-incomplete'); notice.setAttribute('role', 'note');
    notice.append(element('strong', '', t('version.incomplete')), element('p', '', t('version.incompleteDetail')));
    for (const error of snapshot.release_errors) {
      const link = releaseLink(error, t('version.missingRelease', {tag: error.tag}));
      if (link) notice.append(link);
    }
    if (snapshot.notes_error_code) {
      const link = element('a', 'version-release-link', t('version.allReleases'));
      link.href = RELEASES_URL; link.target = '_blank'; link.rel = 'noopener noreferrer'; notice.append(link);
    }
    body.append(notice);
  }
  const releases = snapshot.notes_scope === 'upgrade_range' ? snapshot.releases
    : snapshot.notes_scope === 'latest_only' ? [snapshot.latest] : [];
  for (const item of releases) {
    const section = element('section', 'version-release');
    const heading = element('div', 'version-release-heading');
    heading.append(element('h3', '', `v${item.version}`));
    const link = releaseLink(item); if (link) heading.append(link);
    section.append(heading);
    let hasNotes = false;
    for (const category of CATEGORIES) {
      const entries = item.release?.[category];
      if (!Array.isArray(entries) || !entries.length) continue;
      hasNotes = true;
      const group = element('div', `version-notes version-notes-${category}`);
      const title = element('h4', '', t('version.notes.' + category));
      if (category === 'upgrade' || category === 'breaking') {
        title.prepend(icon(category === 'upgrade' ? 'M12 20V4m-6 6 6-6 6 6M4 18v4h16v-4' : 'm12 3 10 18H2zM12 9v5m0 3v1'));
      }
      const list = element('ul');
      for (const text of entries) if (typeof text === 'string') list.append(element('li', '', text));
      group.append(title, list); section.append(group);
    }
    if (!hasNotes) section.append(element('p', 'version-empty', t('version.emptyNotes')));
    body.append(section);
  }
}
function textButton(key, action, onClick) {
  const node = element('button', 'btn btn-outline version-action', t(key));
  node.type = 'button'; node.dataset.versionAction = action;
  node.addEventListener('click', onClick);
  return node;
}
function commandBlock(kind, command) {
  const block = element('div', 'version-command'); block.dataset.command = kind;
  const pre = element('pre'); pre.append(element('code', '', command));
  const feedback = element('span', 'version-copy-feedback'); feedback.setAttribute('role', 'status');
  const copy = textButton('version.guide.copy', 'copy-' + kind, async () => {
    try { await copyText(command); feedback.textContent = t('version.guide.copied'); }
    catch { feedback.textContent = t('version.guide.copyFailed'); }
  });
  copy.setAttribute('aria-label', t('version.guide.copyLabel', {kind:t('version.guide.' + kind)}));
  block.append(pre, copy, feedback);
  return block;
}
function verifiedTarget() {
  const latest = snapshot?.latest, version = latest?.version;
  return typeof version === 'string' && /^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$/.test(version)
    && latest.tag === `v${version}` && latest.release?.version === version ? version : null;
}
function renderGuide() {
  const method = ['SOURCE','DOCKER'].includes(snapshot?.current.install_method) ? snapshot.current.install_method : 'UNKNOWN';
  const guide = element('section', 'version-guide');
  guide.append(element('h4', '', t('version.guide.title')), element('p', '', t('version.guide.method', {method})));
  const target = !localError && status().startsWith('UPDATE_') ? verifiedTarget() : null;
  if (target && method === 'SOURCE') {
    guide.append(element('p', '', t('version.guide.sourceContext')), element('p', '', t('version.guide.sourceSafety')),
      commandBlock('source', 'git pull --ff-only\nbash bin/install.sh\nbash bin/start.sh'));
  } else if (target && method === 'DOCKER') {
    const image = `ghcr.io/tech-kuiperbelt/remember-me:${target}`;
    guide.append(element('h4', '', 'Docker Compose'), element('p', '', t('version.guide.composeContext')),
      commandBlock('compose', `export REMEMBER_ME_IMAGE=${image}\ndocker compose -f compose.yaml up -d --pull always --wait --wait-timeout 300`),
      element('h4', '', 'Docker Run'), element('p', '', t('version.guide.runContext')),
      commandBlock('run', `export REMEMBER_ME_IMAGE=${image}\ndocker pull --platform linux/amd64 "$REMEMBER_ME_IMAGE"`), element('p', '', t('version.guide.runRecreate')));
  } else if (method === 'UNKNOWN') guide.append(element('p', '', t('version.guide.unknown')));
  if (status() === 'UNAVAILABLE') {
    guide.append(element('p', '', t('version.proxy.help')),
      textButton('version.proxy.configure', 'proxy', () => { close(); configureProxy(); }));
  }
  guide.append(textButton('version.guide.help', 'help', () => { close(); openHelp(); }));
  body.append(guide);
}
function updateRefresh() {
  clearTimeout(cooldownTimer);
  const remaining = Math.max(0, Math.ceil((deadline() - Date.now()) / 1000));
  refreshButton.disabled = checking() || remaining > 0 || (!snapshot && !localError);
  refreshButton.classList.toggle('version-spinning', checking());
  refreshButton.title = remaining ? t('version.cooldown', {seconds: remaining}) : t('version.refresh');
  cooldownNode.textContent = remaining ? t('version.cooldown', {seconds: remaining}) : '';
  cooldownNode.hidden = !remaining;
  if (remaining && !document.hidden && !stopped) cooldownTimer = setTimeout(updateRefresh, 1000);
}
function render() {
  const state = status(), badge = badgeText();
  document.getElementById('version-current').textContent = currentVersion();
  const badgeNode = document.getElementById('version-badge');
  badgeNode.textContent = badge; badgeNode.hidden = !badge;
  entry.dataset.state = state;
  entry.classList.toggle('version-checking', checking() || state === 'CHECKING');
  const label = `${t('version.open')}: ${currentVersion()} · ${t('version.state.' + (checking() ? 'CHECKING' : state))}`;
  entry.setAttribute('aria-label', label); entry.title = label; tooltip.textContent = label;
  statusNode.textContent = t('version.state.' + (checking() ? 'CHECKING' : state));
  statusNode.dataset.state = state;
  const scrollTop = body.scrollTop;
  const focused = body.contains(document.activeElement) ? document.activeElement : null;
  const focusedURL = focused?.closest('a')?.getAttribute('href');
  const focusedAction = focused?.dataset.versionAction;
  body.replaceChildren();
  const versions = element('dl', 'version-comparison');
  for (const [key, value] of [['current', currentVersion()], ['latest', !localError && snapshot?.latest ? `v${snapshot.latest.version}` : t('version.notKnown')]]) {
    const column = element('div'); column.append(element('dt', '', t('version.' + key)), element('dd', '', value)); versions.append(column);
  }
  body.append(versions, element('p', 'version-explanation', explanation()));
  if (!localError && status().startsWith('UPDATE_') && verifiedTarget()) body.append(element('p', 'version-backup-note', t('version.guide.backup')));
  renderNotes();
  renderGuide();
  if (focused && focused !== body) {
    const replacement = focusedAction
      ? [...body.querySelectorAll('[data-version-action]')].find(node => node.dataset.versionAction === focusedAction)
      : [...body.querySelectorAll('a')].find(link => link.getAttribute('href') === focusedURL);
    (replacement || body).focus({preventScroll: true});
  }
  body.scrollTop = scrollTop;
  const date = new Date(snapshot?.check.last_attempt_at || NaN);
  stamp.textContent = t('version.lastChecked', {time: Number.isNaN(date.getTime()) ? t('version.never') : date.toLocaleString(locale)});
  updateRefresh();
  if (!panel.hidden) position();
}
function position() {
  if (panel.hidden) return;
  panel.setAttribute('aria-modal', String(narrow.matches));
  setModalLock(narrow.matches);
  backdrop.hidden = !narrow.matches;
  if (narrow.matches) { panel.style.left = ''; panel.style.top = ''; return; }
  const rect = entry.getBoundingClientRect(), margin = 12;
  panel.style.left = `${Math.max(margin, Math.min(rect.right + 12, innerWidth - panel.offsetWidth - margin))}px`;
  panel.style.top = `${Math.max(margin, Math.min(rect.bottom - panel.offsetHeight, innerHeight - panel.offsetHeight - margin))}px`;
}
let savedInert = false, locked = false;
function setModalLock(enabled) {
  const shell = document.getElementById('app-shell');
  if (enabled && !locked) { savedInert = shell.inert; shell.inert = true; locked = true; }
  if (!enabled && locked) { shell.inert = savedInert; locked = false; }
}
function open() {
  if (!panel.hidden) return;
  panel.hidden = false; entry.setAttribute('aria-expanded', 'true'); tooltip.hidden = true;
  position(); refreshButton.focus({preventScroll: true});
  if (refreshButton.disabled) document.getElementById('version-close').focus({preventScroll: true});
  if (!document.hidden) read();
}
function close() {
  if (panel.hidden) return;
  panel.hidden = true; backdrop.hidden = true;
  setModalLock(false);
  entry.setAttribute('aria-expanded', 'false'); entry.focus({preventScroll: true});
}
function showTooltip() {
  if (!document.getElementById('app-shell').classList.contains('sidebar-collapsed') || !panel.hidden) return;
  tooltip.hidden = false;
  const rect = entry.getBoundingClientRect();
  tooltip.style.left = `${rect.right + 8}px`;
  tooltip.style.top = `${Math.max(8, rect.top - tooltip.offsetHeight)}px`;
}
export function mount({onConfigureProxy, onHelp} = {}) {
  configureProxy = onConfigureProxy || configureProxy; openHelp = onHelp || openHelp;
  entry = document.getElementById('version-entry');
  panel = element('section', 'version-popover'); panel.id = 'version-popover'; panel.hidden = true;
  panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-labelledby', 'version-title');
  const header = element('header', 'version-popover-header');
  const title = element('h2', '', t('version.title')); title.id = 'version-title';
  refreshButton = button('version.refresh', 'M20 7v5h-5M4 17v-5h5M5.6 9a7 7 0 0 1 11.8-2L20 12M4 12l2.6 5a7 7 0 0 0 11.8-2', refresh);
  refreshButton.id = 'version-refresh'; refreshButton.setAttribute('aria-describedby', 'version-cooldown');
  const closeButton = button('version.close', 'm6 6 12 12M6 18 18 6', close); closeButton.id = 'version-close';
  header.append(title, refreshButton, closeButton);
  statusNode = element('div', 'version-state'); statusNode.id = 'version-state'; statusNode.setAttribute('role', 'status');
  cooldownNode = element('p', 'version-cooldown'); cooldownNode.id = 'version-cooldown';
  body = element('div', 'version-popover-body'); body.tabIndex = 0;
  body.setAttribute('role', 'region'); body.setAttribute('aria-label', t('version.notesLabel'));
  const footer = element('footer', 'version-popover-footer'); stamp = element('span'); footer.append(stamp);
  panel.append(header, statusNode, cooldownNode, body, footer);
  backdrop = element('div', 'version-backdrop'); backdrop.hidden = true; backdrop.addEventListener('click', close);
  tooltip = element('div', 'nav-tooltip'); tooltip.hidden = true; tooltip.id = 'version-tooltip'; tooltip.setAttribute('role', 'tooltip');
  document.body.append(backdrop, panel, tooltip);
  entry.addEventListener('click', event => { event.stopPropagation(); panel.hidden ? open() : close(); });
  entry.addEventListener('mouseenter', showTooltip); entry.addEventListener('focus', showTooltip);
  entry.addEventListener('mouseleave', () => { tooltip.hidden = true; });
  entry.addEventListener('blur', () => { tooltip.hidden = true; });
  document.addEventListener('click', event => {
    if (!panel.hidden && !panel.contains(event.target) && !entry.contains(event.target)) close();
  });
  document.addEventListener('keydown', event => {
    if (panel.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    if (event.key !== 'Tab' || !narrow.matches) return;
    const nodes = [...panel.querySelectorAll('button:not(:disabled), a[href], [tabindex="0"]')];
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }, true);
  window.addEventListener('resize', () => { tooltip.hidden = true; position(); });
  new ResizeObserver(position).observe(document.getElementById('nav'));
  document.addEventListener('visibilitychange', () => {
    clearTimeout(pollTimer); clearTimeout(cooldownTimer);
    if (!document.hidden) { updateRefresh(); read(); }
  });
  window.addEventListener('pagehide', () => {
    stopped = true; clearTimeout(pollTimer); clearTimeout(cooldownTimer);
    controllers.forEach(controller => controller.abort());
  });
  window.addEventListener('pageshow', () => { if (stopped) { stopped = false; updateRefresh(); read(); } });
  render(); if (!document.hidden) read();
}
