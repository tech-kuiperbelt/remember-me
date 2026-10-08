// Owns one drawer session. Draft policy, Markdown and candidates have separate owners.
import { $, formatTime, deriveSummary } from './core.js';
import { api, isNamespaceNotFound, confirmNamespaceCreation } from './api.js';
import { toast, toggleDebug } from './ui.js';
import { getNamespaces, getCategories, getCategoryState, onCategoriesChanged, onNamespacesChanged,
  emitMemoriesChanged, emitStatsChanged } from './store.js';
import { createDraft, isDirty, validateDraft, buildUpdatePayload, normalizeAnchors } from './detail-model.js';
import { renderMarkdown, copyText } from './markdown.js';
import { createValuePickers } from './memory-value-picker.js';

let state = 'closed', memory = null, draft = null, selected = null, version = 0;
let picker = null, pending = null, confirmation = null, menu = null, preview = false;
let onClose = null, priorFocus = null, background = null, retryCategories = () => {};
const listeners = new Set();
const busy = () => ['saving', 'reloading', 'deleting'].includes(state);
const editing = () => ['edit', 'saving'].includes(state);
const dirty = () => state === 'edit' && memory && draft && isDirty(memory, draft);
const node = (tag, id, text, cls = '') => { const el = document.createElement(tag); if (id) el.id = id; if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; };
function button(id, text, action, cls = 'btn-outline') {
  const el = node('button', id, text, 'btn btn-sm ' + cls); el.type = 'button'; el.onclick = action; return el;
}
function field(id, title, content) {
  const section = node('section', id, undefined, 'detail-section'); section.append(node('h2', id + '-label', title), content); return section;
}
export const isOpen = () => state !== 'closed';
export function onSelectionChanged(fn) { listeners.add(fn); }
function select(id) { selected = id; listeners.forEach(fn => fn(id)); }
function unload(event) { event.preventDefault(); event.returnValue = ''; }
function updateProtection() {
  window.removeEventListener('beforeunload', unload);
  if (dirty() || busy()) window.addEventListener('beforeunload', unload);
}
function destroyTransient() { picker?.destroy(); picker = null; closeMenu(); }
function closeMenu() { menu?.remove(); menu = null; $('detail-more')?.setAttribute('aria-expanded', 'false'); }
function closeNow() {
  $('detail-body').inert=false; $('detail-header').inert=false;
  ++version; destroyTransient(); confirmation?.remove(); confirmation = null; pending = null;
  state = 'closed'; select(null); updateProtection(); $('detail-modal').style.display = 'none';
  if (background) { $('app-shell').inert = background.inert; document.body.style.overflow = background.overflow; $('content').style.overflow = background.contentOverflow; background = null; }
  const callback = onClose; onClose = null;
  if (callback) callback(); else if (priorFocus?.isConnected) priorFocus.focus();
}
export function requestLeave(action = closeNow) {
  if (busy()) return false;
  if (!dirty()) { if (isOpen()) closeNow(); action === closeNow || action(); return true; }
  if (pending) return false;
  pending = action;
  showConfirm('discard', 'Discard changes?', '未保存的修改将丢失。', 'Keep editing', 'Discard changes', () => {
    const next = pending; pending = null; dismissConfirm(); closeNow(); if (next !== closeNow) next();
  });
  return false;
}
export function openMemory(id, options = {}) {
  return requestLeave(() => startOpen(id, options));
}
function startOpen(id, options) {
  priorFocus = document.activeElement; onClose = options.onClose || null;
  background = {inert: $('app-shell').inert, overflow: document.body.style.overflow, contentOverflow: $('content').style.overflow};
  $('app-shell').inert = true; document.body.style.overflow = 'hidden';
  state = 'loading'; memory = draft = null; select(id); $('detail-modal').style.display = 'flex'; render(); $('btn-detail-close').focus();
  readMemory();
}
async function readMemory(afterSave = false) {
  const request = ++version, id = selected;
  state = afterSave ? 'reloading' : 'loading'; render(); updateProtection();
  try {
    const result = await api('/memories/' + encodeURIComponent(id));
    if (request !== version || !isOpen()) return;
    memory = result; draft = null; state = 'view'; render(); if(afterSave) $('detail-edit').focus();
  } catch(e) {
    if (request !== version || !isOpen()) return;
    state = e.status === 404 ? 'not-found' : afterSave ? 'saved-error' : 'load-error'; render(e.message); if(afterSave) ($('detail-retry') || $('btn-detail-close')).focus();
  }
  updateProtection();
}
function header() {
  const title = memory ? deriveSummary(memory.summary || memory.text) || 'Memory Detail' : 'Memory Detail';
  $('detail-title').textContent = title; $('detail-title').title = title;
  $('detail-subtitle').textContent = memory ? memory.namespace + ' · ' + (formatTime(memory.updated_at) || '—') : '';
  $('detail-subtitle').title = memory?.updated_at || '';
  const actions = $('detail-actions'); actions.replaceChildren();
  if (editing()) {
    actions.append(button('detail-cancel', 'Cancel', () => { destroyTransient(); draft = null; state = 'view'; render(); updateProtection(); $('detail-edit').focus(); }), button('detail-save', 'Save changes', save, 'btn-primary'),button('detail-copy-draft','Copy draft',()=>copy(draft.text)));
  } else if (state === 'view') {
    actions.append(button('detail-edit', 'Edit', edit), button('detail-more', 'More ▾', showMenu));
    $('detail-more').setAttribute('aria-haspopup', 'menu'); $('detail-more').setAttribute('aria-expanded', 'false');
  }
  $('btn-detail-close').disabled = busy();
}
function render(error = '') {
  destroyTransient(); header(); const body = $('detail-body'); body.replaceChildren();
  if (['loading','reloading','deleting'].includes(state)) {
    const status = node('p', 'detail-status', state === 'loading' ? 'Loading memory…' : state === 'deleting' ? 'Deleting…' : '已保存，正在刷新详情…', 'text-muted'); status.setAttribute('role','status'); body.append(status); return;
  }
  if (['load-error','saved-error','not-found'].includes(state)) {
    const message = node('p','detail-status',state === 'saved-error' ? '已保存，详情刷新失败：' + error : state === 'not-found' ? '该记忆已不存在' : '详情加载失败：' + error,'text-danger'); message.setAttribute('role','alert'); body.append(message);
    if (state !== 'not-found') body.append(button('detail-retry','Retry',()=>readMemory(state === 'saved-error'))); return;
  }
  if (editing()) renderEditor(body); else renderView(body);
}
function tags(kind, values) {
  const wrap = node('div', 'detail-' + kind + '-values', undefined, 'detail-tags');
  const paint = expanded => {
    wrap.replaceChildren(); const shown = kind === 'keywords' && !expanded ? values.slice(0,5) : values;
    shown.forEach((value,i) => { const tag = node('span','detail-' + kind + '-' + (i+1),typeof value === 'string' ? value : JSON.stringify(value),'badge kw'); wrap.append(tag); });
    if (!values.length) wrap.append(node('span', 'detail-' + kind + '-empty', '无', 'text-muted'));
    if (kind === 'keywords' && values.length > 5) wrap.append(button('detail-keywords-expand',expanded ? '收起' : '+' + (values.length-5),()=>paint(!expanded)));
  }; paint(false); return wrap;
}
function renderView(body) {
  body.append(field('detail-summary','Summary',node('div','detail-summary-value',memory.summary || '无摘要',memory.summary ? 'detail-plain' : 'text-muted')));
  const content = node('div','detail-content-markdown',undefined,'detail-markdown'); renderMarkdown(content,memory.text);
  body.append(field('detail-content','Content',content));
  for (const kind of ['categories','keywords','anchors']) body.append(field('detail-'+kind,kind[0].toUpperCase()+kind.slice(1),tags(kind,Array.isArray(memory.metadata?.[kind]) ? memory.metadata[kind] : [])));
  body.append(technical());
}
function technical() {
  const details = node('details','detail-technical',undefined,'detail-technical'); const summary=node('summary','detail-technical-toggle','Technical details'); summary.tabIndex=0; details.append(summary);
  const list = node('dl','detail-technical-values');
  for (const [label,value] of [['ID',memory.id],['Created',memory.created_at],['Updated',memory.updated_at]]) { list.append(node('dt','detail-'+label.toLowerCase()+'-label',label),node('dd','detail-'+label.toLowerCase()+'-value',value || '—')); }
  const metadata = node('pre','detail-metadata',JSON.stringify(memory.metadata || {},null,2));
  const debug = button('detail-debug','Debug',()=>toggleDebug()); debug.dataset.debugToggle = ''; debug.setAttribute('aria-pressed',String(document.body.classList.contains('debug-ids')));
  details.append(list,metadata,button('detail-copy-id','Copy ID',()=>copy(memory.id)),debug); return details;
}
function edit() { draft = createDraft(memory); preview = false; state = 'edit'; render(); updateProtection(); $('edit-namespace').focus(); }
function updatePicker() { picker?.update(draft,{namespaces:getNamespaces(),categories:getCategories(),categoryState:getCategoryState()}); }
function renderEditor(body) {
  const status = node('p','detail-error','', 'text-danger'); status.hidden = true; status.setAttribute('role','alert'); body.append(status);
  for (const [key,label] of [['namespace','Namespace'],['summary','Summary']]) {
    const input = node(key === 'namespace' ? 'input' : 'textarea','edit-'+key); input.value = draft[key];
    const section = field('detail-edit-'+key,label,input); section.querySelector('h2').replaceWith(Object.assign(node('label','detail-edit-'+key+'-label',label),{htmlFor:input.id}));
    input.oninput = () => { draft[key] = input.value; if(key === 'namespace') updatePicker(); validate(); }; body.append(section);
    if(key === 'namespace') { input.setAttribute('list','detail-namespaces'); const list = node('datalist','detail-namespaces'); getNamespaces().forEach(ns=>list.append(Object.assign(node('option'),{value:ns}))); section.append(list); }
  }
  const text = node('textarea','edit-text'); text.value = draft.text; text.spellcheck = false; text.setAttribute('aria-label','Content Markdown source');
  const rendered = node('div','detail-preview',undefined,'detail-markdown'); rendered.hidden = true;
  const modes = node('div','detail-content-modes');
  const editButton = button('detail-source-mode','Edit',()=>setPreview(false));
  const previewButton = button('detail-preview-mode','Preview',()=>setPreview(true));
  modes.append(editButton,previewButton); const section = field('detail-edit-content','Content',modes); section.append(text,rendered); body.append(section);
  let sourcePosition = null;
  function setPreview(next) {
    if(next && !preview) sourcePosition = {start:text.selectionStart,end:text.selectionEnd,direction:text.selectionDirection,top:text.scrollTop,left:text.scrollLeft};
    preview = next; if(next) renderMarkdown(rendered,draft.text); text.hidden=next; rendered.hidden=!next; editButton.setAttribute('aria-pressed',String(!next)); previewButton.setAttribute('aria-pressed',String(next));
    if(!next && sourcePosition) { text.setSelectionRange(sourcePosition.start,sourcePosition.end,sourcePosition.direction); text.scrollTop=sourcePosition.top; text.scrollLeft=sourcePosition.left; }
  }
  setPreview(false); text.oninput = () => { draft.text=text.value; validate(); };
  const pickerRoot = node('div','detail-pickers'); body.append(field('detail-edit-tags','Categories & Keywords',pickerRoot));
  picker = createValuePickers({root:pickerRoot,idPrefix:'detail-picker',container:$('detail-panel'),maxSelected:{categories:3,keywords:Infinity},allowCreate:true,onRetryCategories:retryCategories,
    onChange:changes=>{ Object.assign(draft,changes); updatePicker(); validate(); }}); updatePicker();
  const anchors = node('div','detail-anchor-values',undefined,'detail-tags'), input = node('input','detail-anchor-input'); input.placeholder='添加锚点'; input.setAttribute('aria-label','New anchor');
  const add = button('detail-anchor-add','Add',()=>{
    const value = normalizeAnchors([input.value])[0];
    if (!value || Array.from(value).length > 128 || draft.anchors.length >= 5) { showError('最多 5 个锚点，每项 1–128 个字符'); return; }
    if (!normalizeAnchors(draft.anchors).some(existing=>existing===value || (/^[\x00-\x7f]*$/.test(existing) && existing.toLowerCase()===value.toLowerCase()))) draft.anchors.push(value); input.value=''; paintAnchors(); validate();
  });
  input.onkeydown=e=>{ if(e.key==='Enter'&&!e.isComposing){e.preventDefault();add.click();} };
  function paintAnchors() { anchors.replaceChildren(...draft.anchors.map((value,index)=>button('detail-anchor-'+(index+1),String(value)+' ×',()=>{draft.anchors.splice(index,1);paintAnchors();validate();}))); }
  paintAnchors(); const anchorSection = field('detail-edit-anchors','Anchors',anchors); anchorSection.append(input,add,node('p','detail-anchor-hint','修改正文后，请检查锚点是否仍然准确。','text-muted')); body.append(anchorSection,technical()); validate();
}
function showError(message) { const el=$('detail-error'); if(el){el.hidden=!message;el.textContent=message;} else toast(message,'error'); }
function validate() {
  const errors = validateDraft(draft,memory); showError(Object.values(errors).join('；'));
  $('detail-save').disabled = busy() || !isDirty(memory,draft) || Object.keys(errors).length > 0;
  updateProtection();
}
function lock(value) {
  $('detail-panel').querySelectorAll('button,input,textarea,select').forEach(el=>el.disabled=value);
  if(!value && state==='edit') validate(); updateProtection();
}
async function save() {
  if(state!=='edit'||!isDirty(memory,draft)||Object.keys(validateDraft(draft,memory)).length) return;
  picker?.close(); closeMenu(); state='saving'; lock(true); showError(''); const id=selected, request=version, payload=buildUpdatePayload(memory,draft);
  try {
    try { await api('/memories/'+encodeURIComponent(id),{method:'PUT',body:JSON.stringify(payload)}); }
    catch(e) { if(!isNamespaceNotFound(e)) throw e; if(!confirmNamespaceCreation(e)){state='edit';lock(false);return;} await api('/memories/'+encodeURIComponent(id),{method:'PUT',body:JSON.stringify({...payload,confirm_codes:['namespace_not_found']})}); }
    if(request!==version) return;
    state='reloading'; draft=null; emitMemoriesChanged(); emitStatsChanged(); toast('Memory updated'); await readMemory(true);
  } catch(e) { if(request!==version) return; state='edit'; lock(false); showError(e.status===404 ? '该记忆已不存在；可复制草稿后关闭。' : e.message); }
}
async function copy(text) { try { await copyText(text); toast('Copied!'); } catch(e){toast(e.message,'error');} }
function showMenu() {
  if(menu){closeMenu();return;}
  menu=node('div','detail-menu',undefined,'detail-menu'); menu.setAttribute('role','menu');
  menu.append(button('detail-copy','Copy content',()=>{closeMenu();copy(memory.text);}),button('detail-delete','Delete',()=>{closeMenu();showConfirm('delete','Delete this memory?',deriveSummary(memory.summary || memory.text)+'\n'+memory.id,'Cancel','Delete',remove);},'btn-danger'));
  $('detail-panel').append(menu); $('detail-more').setAttribute('aria-expanded','true'); menu.firstChild.focus();
}
function dismissConfirm() { confirmation?.remove(); confirmation=null; pending=null; $('detail-body').inert=false; $('detail-header').inert=false; $('btn-detail-close').focus(); }
function showConfirm(kind,title,text,cancelLabel,acceptLabel,accept) {
  picker?.close(); closeMenu(); confirmation=node('div','detail-confirm',undefined,'detail-confirm-overlay');
  const dialog=node('div','detail-confirm-panel',undefined,'detail-confirm-panel');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-labelledby','detail-confirm-title');
  dialog.append(node('h2','detail-confirm-title',title),node('p','detail-confirm-description',text),node('p','detail-confirm-error','','text-danger'),button('detail-confirm-cancel',cancelLabel,dismissConfirm),button('detail-confirm-'+kind,acceptLabel,accept,'btn-danger'));
  confirmation.append(dialog); $('detail-panel').append(confirmation); $('detail-body').inert=true; $('detail-header').inert=true; $('detail-confirm-cancel').focus();
}
async function remove() {
  if(state==='deleting')return;
  state='deleting'; updateProtection(); confirmation.querySelectorAll('button').forEach(el=>el.disabled=true);
  try { await api('/memories/'+encodeURIComponent(selected),{method:'DELETE'}); closeNow(); emitMemoriesChanged();emitStatsChanged();toast('Memory deleted'); }
  catch(e) { state='view'; updateProtection();confirmation.querySelectorAll('button').forEach(el=>el.disabled=false); $('detail-confirm-error').textContent=e.status===404?'该记忆已不存在，关闭后刷新列表核对。':e.message; }
}
export function mount(options = {}) {
  retryCategories=options.retryCategories || (()=>{});
  $('btn-detail-close').onclick=()=>requestLeave();
  $('detail-modal').onclick=event=>{if(event.target===event.currentTarget)requestLeave();};
  onCategoriesChanged(updatePicker);onNamespacesChanged(updatePicker);
  document.addEventListener('click',event=>{if(menu&&!event.composedPath().includes(menu)&&!event.composedPath().includes($('detail-more')))closeMenu();});
  document.addEventListener('keydown',event=>{
    if(!isOpen()||event.isComposing||event.defaultPrevented)return;
    if(event.key==='Escape') {
      event.preventDefault(); if(busy())return;
      if(picker?.isOpen()){picker.close(true);return;}
      if(menu){closeMenu();$('detail-more').focus();return;}
      if(confirmation){dismissConfirm();return;}
      requestLeave();return;
    }
    if(event.key!=='Tab')return;
    const root=confirmation || picker?.focusRoot() || menu || $('detail-panel');
    const controls=[...root.querySelectorAll('button,input,textarea,select,a[href],summary,[tabindex]')].filter(el=>!el.disabled&&!el.closest('[inert]')&&(!el.closest('details:not([open])') || el.tagName==='SUMMARY')&&el.tabIndex>=0&&el.getClientRects().length);
    const first=controls[0],last=controls.at(-1);
    if(!root.contains(document.activeElement)||(event.shiftKey&&document.activeElement===first)||(!event.shiftKey&&document.activeElement===last)){event.preventDefault();(event.shiftKey?last:first)?.focus();}
  });
}
