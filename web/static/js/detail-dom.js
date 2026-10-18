// Small shared builders for the drawer's view, editor and confirmation surface.
export function node(tag, id, text, cls = '') {
  const el = document.createElement(tag);
  if (id) el.id = id;
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
export function button(id, text, action, cls = 'btn-outline') {
  const el = node('button', id, text, 'btn btn-sm ' + cls);
  el.type = 'button'; el.onclick = action;
  return el;
}
