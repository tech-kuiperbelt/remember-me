// Human-facing onboarding: use the browser's service origin, not a fixed port.
import { $ } from './core.js';
import { copyText } from './markdown.js';
import { toast } from './ui.js';

const topics = new Set(['home', 'connect', 'namespace', 'rules', 'memory', 'retrieval', 'stats', 'configuration', 'troubleshooting']);
let navigate = () => {}, activeTopic = 'home', homePosition = 0, homeEntry = null;
export const normalizeTopic = topic => topics.has(topic) ? topic : 'home';
export function show(topic = 'home', { returnHome = false } = {}) {
  activeTopic = normalizeTopic(topic);
  document.querySelectorAll('[data-help-panel]').forEach(panel => {
    panel.hidden = panel.dataset.helpPanel !== activeTopic;
  });
  const heading = $(activeTopic === 'home' ? 'help-title' : 'help-title-' + activeTopic);
  heading.focus({ preventScroll: true });
  if (returnHome) {
    if (homeEntry?.isConnected) homeEntry.focus({ preventScroll: true });
    $('content').scrollTop = homePosition;
  }
}
export function mount({ onNavigate }) {
  navigate = onNavigate;
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
  $('help-connect-prompt').textContent = `请读取 ${readMe}，按其中的接入指引选择当前 AI 工具支持的连接方式，帮我连接已独立运行的 Remember Me，保留已有配置，避免重复连接。需要我操作时，逐步告诉我怎么做；完成后读取记忆数量验证。若不支持或连接失败，请说明原因。`;
  for (const [button, source] of [['help-copy-url', 'help-read-me-url'], ['help-copy-connect', 'help-connect-prompt'], ['help-copy-system', 'help-system-prompt']]) {
    $(button).addEventListener('click', async () => {
      try {
        await copyText($(source).textContent);
        toast('已复制');
      } catch {
        const container = $(source).closest('details');
        if (container) container.open = true;
        $(source).scrollIntoView({ block: 'nearest' });
        toast('无法访问剪贴板，请选中文本手动复制。', 'error');
      }
    });
  }
  $('help-copy-install').addEventListener('click', async () => {
    try {
      await copyText($('help-install-prompt').textContent + '\n\n以下是要保存的记忆规则：\n\n' + $('help-system-prompt').textContent);
      toast('已复制保存请求和完整记忆规则');
    } catch {
      $('help-install-prompt').closest('details').open = true;
      $('help-system-prompt').closest('details').open = true;
      toast('无法访问剪贴板，请手动复制保存请求和下面的记忆规则。', 'error');
    }
  });
}
