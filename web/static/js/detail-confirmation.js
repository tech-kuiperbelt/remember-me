// Drawer-local confirmation surface; the drawer owns the action and Tab trap.
import { $ } from './core.js';
import { node, button } from './detail-dom.js';

export function createDetailConfirmation({onDismiss}) {
  let root = null;
  function clear() {
    root?.remove(); root = null;
    $('detail-body').inert = false; $('detail-header').inert = false;
  }
  function dismiss() { clear(); onDismiss(); $('btn-detail-close').focus(); }
  function show(kind, title, text, cancelLabel, acceptLabel, accept) {
    clear();
    root = node('div', 'detail-confirm', undefined, 'detail-confirm-overlay');
    const dialog = node('div', 'detail-confirm-panel', undefined, 'detail-confirm-panel');
    dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true'); dialog.setAttribute('aria-labelledby', 'detail-confirm-title');
    dialog.append(node('h2', 'detail-confirm-title', title),
      node('p', 'detail-confirm-description', text), node('p', 'detail-confirm-error', '', 'text-danger'),
      button('detail-confirm-cancel', cancelLabel, dismiss),
      button('detail-confirm-' + kind, acceptLabel, accept, 'btn-danger'));
    root.append(dialog); $('detail-panel').append(root);
    $('detail-body').inert = true; $('detail-header').inert = true; $('detail-confirm-cancel').focus();
  }
  function lock(value) { root?.querySelectorAll('button').forEach(button => { button.disabled = value; }); }
  function error(message) { const output = $('detail-confirm-error'); if (output) output.textContent = message; }
  return {show, clear, dismiss, lock, error, get root() { return root; }};
}
