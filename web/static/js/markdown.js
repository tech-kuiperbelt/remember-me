import { Marked } from '../vendor/marked.js';
import DOMPurify from '../vendor/purify.js';
import { t } from './i18n.js';

const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function safeURL(value) {
  if (!/^(https?:\/\/|mailto:)/i.test(value)) return null;
  try { const url = new URL(value); return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
const parser = new Marked({renderer: {
  html({text}) { return escape(text); },
  link({href, tokens}) {
    const text = this.parser.parseInline(tokens), url = safeURL(href);
    return url ? `<a href="${escape(url)}">${text}</a>` : text;
  },
  image({href, text}) {
    const label = escape(text || t('markdown.image')), url = safeURL(href);
    return url ? `<a href="${escape(url)}">${label}</a>` : label;
  },
}});
export async function copyText(text) {
  if (!navigator.clipboard?.writeText) throw new Error(t('markdown.copyFailed'));
  await navigator.clipboard.writeText(text);
}
export function renderMarkdown(root, source) {
  try {
    root.innerHTML = DOMPurify.sanitize(parser.parse(String(source ?? '')), {
      ALLOWED_TAGS: ['p','br','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','pre','code','table','thead','tbody','tr','th','td','hr','a','strong','em','del'],
      ALLOWED_ATTR: ['href','start'], ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false,
    });
    root.querySelectorAll('a').forEach(link => {
      const url = safeURL(link.getAttribute('href') || '');
      if (!url) { link.replaceWith(...link.childNodes); return; }
      link.href = url;
      if (/^https?:/i.test(url)) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    });
    root.querySelectorAll('pre').forEach((pre, index) => {
      const wrap = document.createElement('div'); wrap.className = 'detail-code'; wrap.id = root.id + '-code-block-' + (index + 1);
      const button = document.createElement('button'); button.id = wrap.id + '-copy'; button.type = 'button'; button.className = 'btn btn-sm btn-outline'; button.textContent = t('markdown.copyCode');
      button.onclick = async () => { try { await copyText(pre.textContent); button.textContent = t('markdown.copied'); } catch(e) { button.textContent = e.message; } };
      pre.before(wrap); wrap.append(button, pre);
    });
    root.querySelectorAll('table').forEach((table, index) => {
      const wrap = document.createElement('div'); wrap.className = 'detail-table'; wrap.id = root.id + '-table-wrap-' + (index + 1); table.before(wrap); wrap.append(table);
    });
  } catch {
    const message = document.createElement('p'); message.className = 'text-danger'; message.textContent = t('markdown.renderFailed');
    const fallback = document.createElement('pre'); fallback.className = 'detail-source'; fallback.textContent = source;
    root.replaceChildren(message, fallback);
  }
  // IDs belong to the renderer, never to untrusted Markdown. Separate roots
  // keep the view and preview namespaces distinct; counters repeat per render.
  const counts = new Map();
  root.querySelectorAll('*').forEach(element => {
    if (element.id) return;
    const tag = element.tagName.toLowerCase(), index = (counts.get(tag) || 0) + 1;
    counts.set(tag, index); element.id = root.id + '-' + tag + '-' + index;
  });
}
