// Human-facing onboarding: use the browser's service origin, not a fixed port.
import { $ } from './core.js';
import { copyText } from './markdown.js';
import { toast } from './ui.js';
import { t, locale } from './i18n.js';

const topics = new Set(['home', 'connect', 'namespace', 'rules', 'memory', 'retrieval', 'stats', 'configuration', 'troubleshooting']);
let navigate = () => {}, activeTopic = 'home', homePosition = 0, homeEntry = null;
export const normalizeTopic = topic => topics.has(topic) ? topic : 'home';
export function show(topic = 'home', { returnHome = false, anchor } = {}) {
  activeTopic = normalizeTopic(topic);
  document.querySelectorAll('[data-help-panel]').forEach(panel => {
    panel.hidden = panel.dataset.helpPanel !== activeTopic;
  });
  const heading = $(activeTopic === 'home' ? 'help-title' : 'help-title-' + activeTopic);
  heading.focus({ preventScroll: true });
  const section = anchor && $(anchor);
  if (section && section.closest('[data-help-panel]')?.dataset.helpPanel === activeTopic) {
    section.tabIndex = -1; section.focus({preventScroll:true}); section.scrollIntoView({block:'start'});
  }
  if (returnHome) {
    if (homeEntry?.isConnected) homeEntry.focus({ preventScroll: true });
    $('content').scrollTop = homePosition;
  }
}
export function mount({ onNavigate }) {
  navigate = onNavigate;
  for (const link of document.querySelectorAll('[data-update-readme]')) {
    const readme = locale === 'zh-CN' ? 'README.zh-CN.md' : 'README.md';
    link.href = `https://github.com/tech-kuiperbelt/remember-me/blob/master/${readme}#${link.dataset.updateReadme}`;
  }
  $('page-help').addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (link.hasAttribute('data-help-topic') || link.hasAttribute('data-help-home')) {
      event.preventDefault();
      if (activeTopic === 'home') { homePosition = $('content').scrollTop; homeEntry = link; }
      navigate(link.dataset.helpTopic || 'home', { returnHome: link.hasAttribute('data-help-home') });
    } else if (link.dataset.helpAnchor) {
      event.preventDefault();
      const target = $(link.dataset.helpAnchor);
      target.setAttribute('tabindex', '-1');
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: 'start' });
    }
  });
  const readMe = new URL('/read-me', location.origin).href;
  $('help-read-me').href = readMe;
  $('help-read-me-url').href = readMe;
  $('help-read-me-url').textContent = readMe;
  $('help-connect-prompt').textContent = t('help.connect.prompt',{url:readMe});
  for (const [button, source] of [['help-copy-url', 'help-read-me-url'], ['help-copy-connect', 'help-connect-prompt'], ['help-copy-system', 'help-system-prompt']]) {
    $(button).addEventListener('click', async () => {
      try {
        await copyText($(source).textContent);
        toast(t('help.toast.copied'));
      } catch {
        const container = $(source).closest('details');
        if (container) container.open = true;
        $(source).scrollIntoView({ block: 'nearest' });
        toast(t('help.toast.copyFailed'), 'error');
      }
    });
  }
  $('help-copy-install').addEventListener('click', async () => {
    try {
      await copyText($('help-install-prompt').textContent + t('help.install.separator') + $('help-system-prompt').textContent);
      toast(t('help.toast.copiedAll'));
    } catch {
      $('help-install-prompt').closest('details').open = true;
      $('help-system-prompt').closest('details').open = true;
      toast(t('help.toast.copyFailedAll'), 'error');
    }
  });
}
