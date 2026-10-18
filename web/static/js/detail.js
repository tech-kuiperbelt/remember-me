// Drawer view and interaction: business state lives in detail-session.js.
import { node, button } from './detail-dom.js';
import { createDrawerAnimator } from './drawer-motion.js';
import { $, formatTime, deriveSummary } from './core.js';
import { createDetailSession } from './detail-session.js';
import { createDetailConfirmation } from './detail-confirmation.js';
import { toast, debugEnabled, toggleDebug } from './ui.js';
import { getNamespaces, getCategories, getCategoryState, onCategoriesChanged, onNamespacesChanged,
  getDefaultNamespace, onDefaultNamespaceChanged, getMaintenance, onMaintenanceChanged, emitMemoriesChanged, emitStatsChanged } from './store.js';
import { renderMarkdown, copyText } from './markdown.js';
import { renderDetailEditor } from './detail-editor.js';
import { renderTechnicalDetails } from './detail-technical.js';
import { createMemoryIcon } from './memory-icons.js';
import { t } from './i18n.js';

const model = createDetailSession();
const {session} = model;
let picker = null, pending = null, menu = null;
const confirmation = createDetailConfirmation({onDismiss: () => { pending = null; }});
let onClose = null, priorFocus = null, background = null, retryCategories = () => {};
const animateDrawer = createDrawerAnimator($('detail-modal'), $('detail-panel'));
let touchedFields = new Set();
const listeners = new Set();
const {busy, editing, hasChanges, dirty} = model;
const draftErrors = model.errors;
function field(id, title, content, kind) {
  const section = node('section', id, undefined, 'detail-section'), label = node('h2', id + '-label', title);
  const dimension = {categories:'category', keywords:'keyword', anchors:'anchor'}[kind];
  if (dimension) { label.classList.add('detail-icon-label'); label.prepend(createMemoryIcon(dimension)); }
  section.append(label, content); return section;
}
export const isOpen = () => session.state !== 'closed';
export function onSelectionChanged(fn) { listeners.add(fn); }
function select(id) { listeners.forEach(fn => fn(id)); }
function unload(event) { event.preventDefault(); event.returnValue = ''; }
function updateProtection() {
  window.removeEventListener('beforeunload', unload);
  if (dirty() || busy()) window.addEventListener('beforeunload', unload);
}
function destroyTransient() { picker?.destroy(); picker = null; closeMenu(); }
function closeMenu() { menu?.remove(); menu = null; $('detail-more')?.setAttribute('aria-expanded', 'false'); }
function closeNow() {
  $('detail-body').inert=false; $('detail-header').inert=false;
  destroyTransient(); confirmation.clear(); pending = null;
  model.close(); select(null); updateProtection(); animateDrawer(false);
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
  model.open(id);
  priorFocus = document.activeElement; onClose = options.onClose || null;
  background = {inert: $('app-shell').inert, overflow: document.body.style.overflow, contentOverflow: $('content').style.overflow};
  $('app-shell').inert = true; document.body.style.overflow = 'hidden';
  select(id); animateDrawer(true); render(); $('btn-detail-close').focus();
  readMemory();
}
async function readMemory(afterSave = false) {
  const reading = model.read(afterSave);
  render(); updateProtection();
  if (!await reading) return;
  render(session.error?.message);
  if (afterSave) ($('detail-edit') || $('detail-retry') || $('btn-detail-close')).focus();
  updateProtection();
}
function header() {
  const title = session.mode === 'create' ? t('detail.title.new') : session.memory ? deriveSummary(session.memory.summary || session.memory.text) || t('detail.title') : t('detail.title');
  $('detail-title').replaceChildren(createMemoryIcon('memory'), node('span','detail-title-text',title)); $('detail-title').title = title;
  $('detail-subtitle').textContent = session.memory ? session.memory.namespace + ' · ' + (formatTime(session.memory.updated_at) || '—') : '';
  if (session.memory?.namespace) $('detail-subtitle').prepend(createMemoryIcon('namespace'));
  $('detail-subtitle').title = session.memory?.updated_at || '';
  const actions = $('detail-actions'); actions.replaceChildren();
  if (editing()) {
    actions.append(button('detail-cancel', t('detail.action.cancel'), () => { if(session.mode==='create'){requestLeave();return;} destroyTransient(); model.cancelEdit(); render(); updateProtection(); $('detail-edit').focus(); }), button('detail-save', session.mode==='create' ? t('detail.action.create') : t('detail.action.save'), save, 'btn-primary'),button('detail-copy-draft',t('detail.action.copyDraft'),()=>copy(session.draft.text)));
  } else if (session.state === 'view') {
    actions.append(button('detail-edit', t('detail.action.edit'), edit, 'btn-primary'), button('detail-more', t('detail.action.more'), showMenu));
    $('detail-more').setAttribute('aria-haspopup', 'menu'); $('detail-more').setAttribute('aria-expanded', 'false');
  }
  if (debugEnabled()) actions.append(debugButton());
  $('btn-detail-close').disabled = busy();
}
function render(error = '') {
  destroyTransient(); header(); const body = $('detail-body'); body.replaceChildren();
  if (['loading','reloading','deleting'].includes(session.state)) {
    const status = node('p', 'detail-status', session.state === 'loading' ? t('detail.status.loading') : session.state === 'deleting' ? t('detail.status.deleting') : session.writeOutcome === 'created' ? t('detail.status.createdLoading') : t('detail.status.savedLoading'), 'text-muted'); status.setAttribute('role','status'); body.append(status); return;
  }
  if (['load-error','saved-error','not-found'].includes(session.state)) {
    const message = node('p','detail-status',session.state === 'saved-error' ? (session.writeOutcome === 'created' ? t('detail.status.createdLoadFailed',{error}) : t('detail.status.savedLoadFailed',{error})) : session.state === 'not-found' ? t('detail.status.notFound') : t('detail.status.loadFailed',{error}),'text-danger'); message.setAttribute('role','alert'); body.append(message);
    if (session.state !== 'not-found') body.append(button('detail-retry',t('detail.action.retry'),()=>readMemory(session.state === 'saved-error'))); return;
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
  body.append(field('detail-summary',t('detail.field.summary'),node('div','detail-summary-value',session.memory.summary || '—',session.memory.summary ? 'detail-plain' : 'text-muted')));
  const content = node('div','detail-content-markdown',undefined,'detail-markdown'); renderMarkdown(content,session.memory.text);
  body.append(field('detail-content',t('detail.field.content'),content));
  for (const kind of ['categories','keywords','anchors']) body.append(field('detail-'+kind,t('detail.field.'+kind),tags(kind,Array.isArray(session.memory.metadata?.[kind]) ? session.memory.metadata[kind] : []),kind));
  body.append(technical());
}
function technical() { return renderTechnicalDetails(session.memory); }
function edit() { model.edit(); render(); updateProtection(); $('edit-namespace').focus(); }
function updatePicker() { picker?.update(session.draft,{namespaces:getNamespaces(),categories:getCategories(),categoryState:getCategoryState()}); }
function renderEditor(body) {
  touchedFields = new Set();
  picker = renderDetailEditor({body, draft: session.draft, creating: session.mode === 'create', validate, showError, technical, retryCategories, onRetryDefault: retryCategories});
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
  if(session.state!=='edit')return;
  $('detail-save').disabled = (session.mode==='create' ? getMaintenance()?.state !== 'normal' : !hasChanges()) || Object.keys(draftErrors()).length > 0;
}
function lock(value) {
  $('detail-panel').querySelectorAll('button,input,textarea,select').forEach(el=>el.disabled=value);
  const saveButton=$('detail-save');
  if(saveButton) saveButton.textContent=value ? (session.mode==='create' ? t('detail.action.creating') : t('detail.action.saving')) : (session.mode==='create' ? t('detail.action.create') : t('detail.action.save'));
  if(!value && session.state==='edit') validate(); updateProtection();
}
async function save() {
  if(session.state!=='edit')return;
  const errors=validate(null,true);
  if(Object.keys(errors).length) { $({text:'edit-text',categories:'detail-picker-categories',keywords:'detail-picker-keywords',anchors:'detail-anchor-input'}[Object.keys(errors)[0]] || 'edit-'+Object.keys(errors)[0])?.focus(); return; }
  const creating = session.mode === 'create';
  if (creating ? getMaintenance()?.state !== 'normal' : !hasChanges()) return;
  picker?.close(); closeMenu(); $('detail-duplicate')?.remove();
  const writing = model.save(); lock(true); showError('');
  const outcome = await writing;
  if (!outcome) return;
  if (outcome === 'created' || outcome === 'updated') {
    if (creating) select(session.selected);
    emitMemoriesChanged(); emitStatsChanged(); toast(t('detail.toast.' + outcome));
    await readMemory(true); return;
  }
  lock(false);
  if (outcome === 'duplicate') {
    const notice=node('div','detail-duplicate'); notice.setAttribute('role','status');
    notice.append(node('p','detail-duplicate-message',t('detail.duplicate.message')),button('detail-view-existing',t('detail.duplicate.view'),()=>openMemory(session.duplicateId,{onClose})));
    $('detail-body').prepend(notice);
  } else if (outcome === 'error') {
    const error = session.error;
    showError(creating ? (!error.status ? t('detail.error.writeUnconfirmed',{error:error.message}) : error.message)
      : error.status === 404 ? t('detail.error.notFoundCopy') : error.message);
  }
}
async function copy(text) { try { await copyText(text); toast(t('detail.toast.copied')); } catch(e){toast(e.message,'error');} }
function showMenu() {
  if(menu){closeMenu();return;}
  menu=node('div','detail-menu',undefined,'detail-menu'); menu.setAttribute('role','menu');
  const copySummary=button('detail-copy-summary',t('detail.menu.copySummary'),()=>{closeMenu();copy(session.memory.summary);});
  copySummary.disabled=!session.memory.summary;
  menu.append(copySummary,button('detail-copy',t('detail.menu.copyContent'),()=>{closeMenu();copy(session.memory.text);}),button('detail-delete',t('detail.menu.delete'),()=>{closeMenu();showConfirm('delete',t('detail.confirm.delete.title'),deriveSummary(session.memory.summary || session.memory.text)+'\n'+session.memory.id,t('detail.confirm.delete.cancel'),t('detail.confirm.delete.accept'),remove);},'btn-danger'));
  $('detail-panel').append(menu); $('detail-more').setAttribute('aria-expanded','true'); menu.querySelector('button:not(:disabled)').focus();
}
function dismissConfirm() { confirmation.dismiss(); }
function showConfirm(...args) { picker?.close(); closeMenu(); confirmation.show(...args); }
async function remove() {
  if (busy()) return;
  const removing = model.remove(); updateProtection(); confirmation.lock(true);
  const deletedId = await removing;
  if (deletedId) {
    closeNow(); emitMemoriesChanged({deletedId}); emitStatsChanged(); toast(t('detail.toast.deleted'));
  } else if (session.error) {
    updateProtection(); confirmation.lock(false);
    confirmation.error(session.error.status === 404 ? t('detail.error.notFoundDelete') : session.error.message);
  }
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
      if(confirmation.root){dismissConfirm();return;}
      requestLeave();return;
    }
    if(event.key!=='Tab')return;
    const root=confirmation.root || picker?.focusRoot() || menu || $('detail-panel');
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
    priorFocus=document.activeElement;onClose=options.onClose || null;
    background={inert:$('app-shell').inert,overflow:document.body.style.overflow,contentOverflow:$('content').style.overflow};
    $('app-shell').inert=true;document.body.style.overflow='hidden';
    const configured=getDefaultNamespace();
    model.create(namespace || (configured.status==='ready' ? configured.value : ''));
    select(null);
    animateDrawer(true);render();$('edit-namespace').focus();
  });
}
function updateDefaultHint() {
  if(session.mode!=='create'||session.state!=='edit')return;
  const configured=getDefaultNamespace(), input=$('edit-namespace');
  if(configured.status==='ready' && !session.draft.namespace && !session.initialDraft.namespace && input && !input.dataset.touched) {
    model.useDefaultNamespace(configured.value);input.value=configured.value;updatePicker();validate();
  }
  const hint=$('detail-default-namespace-status'), retry=$('detail-default-namespace-retry');
  if(hint) hint.textContent=configured.status==='ready' ? '' : configured.status==='error' ? t('detail.defaultNamespace.error') : t('detail.defaultNamespace.loading');
  if(retry)retry.hidden=configured.status!=='error';
}
