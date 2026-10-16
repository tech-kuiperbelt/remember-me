// Owns one drawer session. Draft policy, Markdown and candidates have separate owners.
import { createDrawerAnimator } from './drawer-motion.js';
import { $, formatTime, deriveSummary } from './core.js';
import { api, isNamespaceNotFound, confirmNamespaceCreation } from './api.js';
import { toast, debugEnabled, toggleDebug } from './ui.js';
import { getNamespaces, getCategories, getCategoryState, onCategoriesChanged, onNamespacesChanged,
  getDefaultNamespace, onDefaultNamespaceChanged, getMaintenance, onMaintenanceChanged, emitMemoriesChanged, emitStatsChanged } from './store.js';
import { createDraft, isDirty, validateDraft, buildUpdatePayload, createNewDraft, validateCreateDraft, isCreateDirty, buildCreatePayload } from './detail-model.js';
import { renderMarkdown, copyText } from './markdown.js';
import { renderDetailEditor } from './detail-editor.js';
import { renderTechnicalDetails } from './detail-technical.js';
import { createMemoryIcon } from './memory-icons.js';
import { t } from './i18n.js';

let state = 'closed', memory = null, draft = null, selected = null, version = 0;
let picker = null, pending = null, confirmation = null, menu = null, preview = false;
let onClose = null, priorFocus = null, background = null, retryCategories = () => {};
const animateDrawer = createDrawerAnimator($('detail-modal'), $('detail-panel'));
let touchedFields = new Set();
const listeners = new Set();
const busy = () => ['saving', 'reloading', 'deleting'].includes(state);
const editing = () => ['edit', 'saving'].includes(state);
let mode = 'detail', initialDraft = null, duplicateId = null, created = false;
const hasChanges = () => draft && (mode === 'create' ? isCreateDirty(initialDraft, draft) : memory && isDirty(memory,draft));
const draftErrors = () => mode === 'create' ? validateCreateDraft(draft) : validateDraft(draft,memory);
const dirty = () => state === 'edit' && hasChanges();
const node = (tag, id, text, cls = '') => { const el = document.createElement(tag); if (id) el.id = id; if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; };
function button(id, text, action, cls = 'btn-outline') {
  const el = node('button', id, text, 'btn btn-sm ' + cls); el.type = 'button'; el.onclick = action; return el;
}
function field(id, title, content) {
  const section = node('section', id, undefined, 'detail-section'), label = node('h2', id + '-label', title);
  const dimension = {Categories:'category', Keywords:'keyword', Anchors:'anchor'}[title];
  if (dimension) { label.classList.add('detail-icon-label'); label.prepend(createMemoryIcon(dimension)); }
  section.append(label, content); return section;
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
  state = 'closed'; select(null); updateProtection(); animateDrawer(false);
  if (background) { $('app-shell').inert = background.inert; document.body.style.overflow = background.overflow; $('content').style.overflow = background.contentOverflow; background = null; }
  const callback = onClose; onClose = null;
  if (callback) callback(); else if (priorFocus?.isConnected) priorFocus.focus();
}
export function requestLeave(action = closeNow) {
  if (busy()) return false;
  if (!dirty()) { if (isOpen()) closeNow(); action === closeNow || action(); return true; }
  if (pending) return false;
  pending = action;
  showConfirm('discard', t('detail.confirm.discard.title'), t('detail.confirm.discard.text'), t('detail.confirm.discard.cancel'), t('detail.confirm.discard.accept'), () => {
    const next = pending; pending = null; dismissConfirm(); closeNow(); if (next !== closeNow) next();
  });
  return false;
}
export function openMemory(id, options = {}) {
  return requestLeave(() => startOpen(id, options));
}
function startOpen(id, options) {
  mode='detail'; created=false; duplicateId=null; initialDraft=null;
  priorFocus = document.activeElement; onClose = options.onClose || null;
  background = {inert: $('app-shell').inert, overflow: document.body.style.overflow, contentOverflow: $('content').style.overflow};
  $('app-shell').inert = true; document.body.style.overflow = 'hidden';
  state = 'loading'; memory = draft = null; select(id); animateDrawer(true); render(); $('btn-detail-close').focus();
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
  const title = mode === 'create' ? t('detail.title.new') : memory ? deriveSummary(memory.summary || memory.text) || t('detail.title') : t('detail.title');
  $('detail-title').replaceChildren(createMemoryIcon('memory'), node('span','detail-title-text',title)); $('detail-title').title = title;
  $('detail-subtitle').textContent = memory ? memory.namespace + ' · ' + (formatTime(memory.updated_at) || '—') : '';
  if (memory?.namespace) $('detail-subtitle').prepend(createMemoryIcon('namespace'));
  $('detail-subtitle').title = memory?.updated_at || '';
  const actions = $('detail-actions'); actions.replaceChildren();
  if (editing()) {
    actions.append(button('detail-cancel', t('detail.action.cancel'), () => { if(mode==='create'){requestLeave();return;} destroyTransient(); draft = null; state = 'view'; render(); updateProtection(); $('detail-edit').focus(); }), button('detail-save', mode==='create' ? t('detail.action.create') : t('detail.action.save'), save, 'btn-primary'),button('detail-copy-draft',t('detail.action.copyDraft'),()=>copy(draft.text)));
  } else if (state === 'view') {
    actions.append(button('detail-edit', t('detail.action.edit'), edit, 'btn-primary'), button('detail-more', t('detail.action.more'), showMenu));
    $('detail-more').setAttribute('aria-haspopup', 'menu'); $('detail-more').setAttribute('aria-expanded', 'false');
  }
  if (debugEnabled()) actions.append(debugButton());
  $('btn-detail-close').disabled = busy();
}
function render(error = '') {
  destroyTransient(); header(); const body = $('detail-body'); body.replaceChildren();
  if (['loading','reloading','deleting'].includes(state)) {
    const status = node('p', 'detail-status', state === 'loading' ? t('detail.status.loading') : state === 'deleting' ? t('detail.status.deleting') : created ? t('detail.status.createdLoading') : t('detail.status.savedLoading'), 'text-muted'); status.setAttribute('role','status'); body.append(status); return;
  }
  if (['load-error','saved-error','not-found'].includes(state)) {
    const message = node('p','detail-status',state === 'saved-error' ? (created ? t('detail.status.createdLoadFailed',{error}) : t('detail.status.savedLoadFailed',{error})) : state === 'not-found' ? t('detail.status.notFound') : t('detail.status.loadFailed',{error}),'text-danger'); message.setAttribute('role','alert'); body.append(message);
    if (state !== 'not-found') body.append(button('detail-retry',t('detail.action.retry'),()=>readMemory(state === 'saved-error'))); return;
  }
  if (editing()) renderEditor(body); else renderView(body);
}
function tags(kind, values) {
  const wrap = node('div', 'detail-' + kind + '-values', undefined, 'detail-tags');
  values.forEach((value,i) => wrap.append(node('span','detail-'+kind+'-'+(i+1),typeof value==='string' ? value : JSON.stringify(value),'detail-tag detail-tag-'+kind)));
  if (!values.length) wrap.append(node('span','detail-'+kind+'-empty','—','text-muted'));
  return wrap;
}
function renderView(body) {
  body.append(field('detail-summary',t('detail.field.summary'),node('div','detail-summary-value',memory.summary || '—',memory.summary ? 'detail-plain' : 'text-muted')));
  const content = node('div','detail-content-markdown',undefined,'detail-markdown'); renderMarkdown(content,memory.text);
  body.append(field('detail-content',t('detail.field.content'),content));
  for (const kind of ['categories','keywords','anchors']) body.append(field('detail-'+kind,t('detail.field.'+kind),tags(kind,Array.isArray(memory.metadata?.[kind]) ? memory.metadata[kind] : [])));
  body.append(technical());
}
function technical() { return renderTechnicalDetails(memory); }
function edit() { created=false; draft = createDraft(memory); preview = false; state = 'edit'; render(); updateProtection(); $('edit-namespace').focus(); }
function updatePicker() { picker?.update(draft,{namespaces:getNamespaces(),categories:getCategories(),categoryState:getCategoryState()}); }
function renderEditor(body) {
  touchedFields = new Set();
  picker = renderDetailEditor({body, draft, creating: mode === 'create', validate, showError, technical, retryCategories, onRetryDefault: retryCategories});
  updateDefaultHint();
}

function showError(message) { const el=$('detail-error'); if(el){el.hidden=!message;el.textContent=message;} else toast(message,'error'); }
function validate(key, reveal = false) {
  const errors = draftErrors();
  if (key && reveal) touchedFields.add(key);
  if (!key && reveal) Object.keys(errors).forEach(field => touchedFields.add(field));
  if (key) showError('');
  for (const field of ['namespace','summary','text','categories','keywords','anchors']) {
    const message = touchedFields.has(field) ? errors[field] || '' : '';
    const output = $('detail-field-error-' + field);
    if (output) { output.textContent = message; output.hidden = !message; }
    const control = $({text:'edit-text',categories:'detail-picker-categories',keywords:'detail-picker-keywords',anchors:'detail-anchor-input'}[field] || 'edit-' + field);
    control?.setAttribute('aria-invalid', String(Boolean(message)));
  }
  updateSaveAvailability(); updateProtection();
  return errors;
}
function updateSaveAvailability() {
  if(state!=='edit')return;
  $('detail-save').disabled = (mode==='create' ? getMaintenance()?.state !== 'normal' : !hasChanges()) || Object.keys(draftErrors()).length > 0;
}
function lock(value) {
  $('detail-panel').querySelectorAll('button,input,textarea,select').forEach(el=>el.disabled=value);
  const saveButton=$('detail-save');
  if(saveButton) saveButton.textContent=value ? (mode==='create' ? t('detail.action.creating') : t('detail.action.saving')) : (mode==='create' ? t('detail.action.create') : t('detail.action.save'));
  if(!value && state==='edit') validate(); updateProtection();
}
async function save() {
  if(state!=='edit')return;
  const errors=validate(null,true);
  if(Object.keys(errors).length) { $({text:'edit-text',categories:'detail-picker-categories',keywords:'detail-picker-keywords',anchors:'detail-anchor-input'}[Object.keys(errors)[0]] || 'edit-'+Object.keys(errors)[0])?.focus(); return; }
  if(mode==='create'){await createMemory();return;}
  if(!hasChanges())return;
  picker?.close(); closeMenu(); state='saving'; lock(true); showError(''); const id=selected, request=version, payload=buildUpdatePayload(memory,draft);
  try {
    try { await api('/memories/'+encodeURIComponent(id),{method:'PUT',body:JSON.stringify(payload)}); }
    catch(e) { if(!isNamespaceNotFound(e)) throw e; if(!confirmNamespaceCreation(e)){state='edit';lock(false);return;} await api('/memories/'+encodeURIComponent(id),{method:'PUT',body:JSON.stringify({...payload,confirm_codes:['namespace_not_found']})}); }
    if(request!==version) return;
    state='reloading'; draft=null; emitMemoriesChanged(); emitStatsChanged(); toast(t('detail.toast.updated')); await readMemory(true);
  } catch(e) { if(request!==version) return; state='edit'; lock(false); showError(e.status===404 ? t('detail.error.notFoundCopy') : e.message); }
}
async function copy(text) { try { await copyText(text); toast(t('detail.toast.copied')); } catch(e){toast(e.message,'error');} }
function showMenu() {
  if(menu){closeMenu();return;}
  menu=node('div','detail-menu',undefined,'detail-menu'); menu.setAttribute('role','menu');
  const copySummary=button('detail-copy-summary',t('detail.menu.copySummary'),()=>{closeMenu();copy(memory.summary);});
  copySummary.disabled=!memory.summary;
  menu.append(copySummary,button('detail-copy',t('detail.menu.copyContent'),()=>{closeMenu();copy(memory.text);}),button('detail-delete',t('detail.menu.delete'),()=>{closeMenu();showConfirm('delete',t('detail.confirm.delete.title'),deriveSummary(memory.summary || memory.text)+'\n'+memory.id,t('detail.confirm.delete.cancel'),t('detail.confirm.delete.accept'),remove);},'btn-danger'));
  $('detail-panel').append(menu); $('detail-more').setAttribute('aria-expanded','true'); menu.querySelector('button:not(:disabled)').focus();
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
  try { const deletedId=selected; await api('/memories/'+encodeURIComponent(deletedId),{method:'DELETE'}); closeNow(); emitMemoriesChanged({deletedId});emitStatsChanged();toast(t('detail.toast.deleted')); }
  catch(e) { state='view'; updateProtection();confirmation.querySelectorAll('button').forEach(el=>el.disabled=false); $('detail-confirm-error').textContent=e.status===404?t('detail.error.notFoundDelete'):e.message; }
}
export function mount(options = {}) {
  retryCategories=options.retryCategories || (()=>{});
  $('btn-detail-close').onclick=()=>requestLeave();
  $('detail-modal').onclick=event=>{if(event.target===event.currentTarget)requestLeave();};
  onCategoriesChanged(updatePicker);onNamespacesChanged(updatePicker);
  onDefaultNamespaceChanged(updateDefaultHint);
  onMaintenanceChanged(updateSaveAvailability);
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

function debugButton() {
  const debug=button('detail-debug',t('detail.debug'),()=>toggleDebug());debug.dataset.debugToggle='';
  debug.setAttribute('aria-pressed',String(document.body.classList.contains('debug-ids')));return debug;
}
export function openCreate(namespace = '', options = {}) {
  return requestLeave(() => {
    mode='create';created=false;duplicateId=null;memory=null;
    priorFocus=document.activeElement;onClose=options.onClose || null;
    background={inert:$('app-shell').inert,overflow:document.body.style.overflow,contentOverflow:$('content').style.overflow};
    $('app-shell').inert=true;document.body.style.overflow='hidden';
    const configured=getDefaultNamespace();
    draft=createNewDraft(namespace || (configured.status==='ready' ? configured.value : ''));
    initialDraft=structuredClone(draft);select(null);state='edit';++version;
    animateDrawer(true);render();$('edit-namespace').focus();
  });
}
function updateDefaultHint() {
  if(mode!=='create'||state!=='edit')return;
  const configured=getDefaultNamespace(), input=$('edit-namespace');
  if(configured.status==='ready' && !draft.namespace && !initialDraft.namespace && input && !input.dataset.touched) {
    draft.namespace=configured.value;initialDraft.namespace=configured.value;input.value=configured.value;updatePicker();validate();
  }
  const hint=$('detail-default-namespace-status'), retry=$('detail-default-namespace-retry');
  if(hint) hint.textContent=configured.status==='ready' ? '' : configured.status==='error' ? t('detail.defaultNamespace.error') : t('detail.defaultNamespace.loading');
  if(retry)retry.hidden=configured.status!=='error';
}
async function createMemory() {
  if(getMaintenance()?.state!=='normal')return;
  picker?.close();closeMenu();duplicateId=null;$('detail-duplicate')?.remove();
  state='saving';lock(true);showError('');const request=version,payload=buildCreatePayload(draft);
  try {
    let result;
    try {result=await api('/memories',{method:'POST',body:JSON.stringify(payload)});}
    catch(e) {if(!isNamespaceNotFound(e))throw e;if(!confirmNamespaceCreation(e)){state='edit';lock(false);return;}result=await api('/memories',{method:'POST',body:JSON.stringify({...payload,confirm_codes:['namespace_not_found']})});}
    if(request!==version)return;
    if(!result || typeof result.id!=='string' || typeof result.created!=='boolean')throw new Error(t('detail.error.createIncomplete'));
    if(!result.created) {
      duplicateId=result.id;state='edit';lock(false);
      const notice=node('div','detail-duplicate');notice.setAttribute('role','status');
      notice.append(node('p','detail-duplicate-message',t('detail.duplicate.message')),button('detail-view-existing',t('detail.duplicate.view'),()=>openMemory(duplicateId,{onClose})));
      $('detail-body').prepend(notice);return;
    }
    created=true;mode='detail';select(result.id);draft=null;state='reloading';
    emitMemoriesChanged();emitStatsChanged();toast(t('detail.toast.created'));await readMemory(true);
  } catch(e) {
    if(request!==version)return;state='edit';lock(false);
    showError(!e.status ? t('detail.error.writeUnconfirmed',{error:e.message}) : e.message);
  }
}
