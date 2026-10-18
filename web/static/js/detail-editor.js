// Shared create/update form; the drawer owns requests, dirty state and focus.
import { node, button } from './detail-dom.js';
import { $ } from './core.js';
import { getNamespaces, getDefaultNamespace, getCategories, getCategoryState } from './store.js';
import { normalizeAnchors } from './detail-model.js';
import { renderMarkdown } from './markdown.js';
import { createValuePickers } from './memory-value-picker.js';
import { createNamespacePicker } from './namespace-picker.js';
import { createMemoryIcon } from './memory-icons.js';
import { t } from './i18n.js';
function field(id,title,content) {
  const section=node('section',id,undefined,'detail-section'), label=node('h2',id+'-label',title);
  const dimension = id.endsWith('categories') ? 'category' : id.endsWith('keywords') ? 'keyword' : id.endsWith('anchors') ? 'anchor' : null;
  if(dimension){label.classList.add('detail-icon-label');label.prepend(createMemoryIcon(dimension));}
  section.append(label,content);return section;
}
export function renderDetailEditor({body, draft, creating, validate, showError, technical, retryCategories, onRetryDefault}) {
  let picker;
  let namespacePicker;
  let preview = false;
  let refreshNamespaces = () => {};
  const updatePicker = () => picker?.update(draft,{namespaces:getNamespaces(),categories:getCategories(),categoryState:getCategoryState()});
  let contentHeight = 240;
  function resize(input, min, max) {
    if (input.hidden || !input.isConnected) return;
    const scroll = input.scrollTop;
    input.style.height = 'auto';
    const height = Math.max(min, Math.min(max, input.scrollHeight + 2));
    input.style.height = height + 'px'; input.scrollTop = scroll;
    return height;
  }
  function fieldError(section, key, control) {
    const error=node('p','detail-field-error-'+key,'','detail-field-error');error.hidden=true;
    section.append(error);control?.setAttribute('aria-describedby',error.id);
    if(control) control.onblur=()=>validate(key,true);
  }
  const status = node('p','detail-error','', 'text-danger'); status.hidden = true; status.setAttribute('role','alert'); body.append(status);
  for (const [key,label] of [['namespace',t('detail.field.namespace')],['summary',t('detail.field.summary')]]) {
    const input = node(key === 'namespace' ? 'input' : 'textarea','edit-'+key); input.value = draft[key];
    const section = field('detail-edit-'+key,label,input); section.querySelector('h2').replaceWith(Object.assign(node('label','detail-edit-'+key+'-label',label),{htmlFor:input.id}));
    if(key==='namespace'){const label=section.querySelector('label');label.classList.add('detail-icon-label');label.prepend(createMemoryIcon('namespace'));}
    input.oninput = () => { draft[key] = input.value; if(key==='summary')resize(input,64,130); if(key === 'namespace') {namespacePicker?.close();input.dataset.touched='true';updatePicker();} validate(key); }; fieldError(section,key,input); body.append(section);
    if(key==='summary'){input.rows=2;input.placeholder=t('detail.summary.placeholder');}
    if(key === 'summary' && creating) section.append(node('p','detail-summary-hint',t('detail.summary.hint'),'text-muted'));
    if(key === 'namespace' && creating) {
      const hint=node('p','detail-default-namespace-status','','text-muted'); hint.setAttribute('role','status'); section.append(hint,button('detail-default-namespace-retry',t('detail.defaultNamespace.retry'),onRetryDefault));
    }
    if(key === 'namespace') {
      const control=node('div','detail-namespace-control',undefined,'detail-namespace-control');
      input.replaceWith(control); control.append(input); input.autocomplete='off';
      namespacePicker=createNamespacePicker({control,input,onOpen:()=>picker?.close(),onChoose:value=>{
        input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));
      }});
      const initialNamespace=draft.namespace;
      refreshNamespaces = () => {
        const configured=getDefaultNamespace();
        const values=[...new Set([...getNamespaces(), initialNamespace,
          configured.status==='ready' ? configured.value : ''].filter(value=>typeof value==='string'&&value.trim()))];
        namespacePicker.update(values);
      };
      refreshNamespaces();
    }
  }
  const text = node('textarea','edit-text'); text.value = draft.text; text.spellcheck = false; text.setAttribute('aria-label',t('detail.content.sourceAria'));
  const rendered = node('div','detail-preview',undefined,'detail-markdown'); rendered.hidden = true;
  const modes = node('div','detail-content-modes');
  modes.setAttribute('role','tablist'); modes.setAttribute('aria-label',t('detail.content.aria'));
  const editButton = button('detail-source-mode',t('detail.content.edit'),()=>setPreview(false)); editButton.className='detail-content-tab';
  const previewButton = button('detail-preview-mode',t('detail.content.preview'),()=>setPreview(true)); previewButton.className='detail-content-tab';
  const source = node('div','detail-source-panel'); source.append(text);
  for (const [tab,panel] of [[editButton,source],[previewButton,rendered]]) {
    tab.setAttribute('role','tab'); tab.setAttribute('aria-controls',panel.id);
    panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby',tab.id);
  }
  rendered.tabIndex=0;
  modes.append(editButton,previewButton);
  const editor = node('div','detail-content-editor',undefined,'detail-content-editor'); editor.append(modes,source,rendered);
  const contentSection=field('detail-edit-content',t('detail.field.content'),editor);fieldError(contentSection,'text',text);body.append(contentSection);
  text.placeholder=t('detail.content.placeholder');
  modes.onkeydown = event => {
    if(event.isComposing || event.target.disabled) return;
    const tabs=[editButton,previewButton], index=tabs.indexOf(event.target);
    if(index<0) return;
    let next;
    if(event.key==='ArrowRight'||event.key==='ArrowLeft') next=1-index;
    else if(event.key==='Home') next=0;
    else if(event.key==='End') next=1;
    else return;
    event.preventDefault(); setPreview(next===1); tabs[next].focus();
  };
  let sourcePosition = null;
  function setPreview(next) {
    if(next && !preview) sourcePosition = {start:text.selectionStart,end:text.selectionEnd,direction:text.selectionDirection,top:text.scrollTop,left:text.scrollLeft};
    preview = next; if(next) { renderMarkdown(rendered,draft.text);rendered.style.height=contentHeight+'px'; } source.hidden=next; text.hidden=next; rendered.hidden=!next;
    editButton.setAttribute('aria-selected',String(!next)); previewButton.setAttribute('aria-selected',String(next));
    editButton.tabIndex=next?-1:0; previewButton.tabIndex=next?0:-1;
    if(!next) contentHeight=resize(text,240,500)||contentHeight;
    if(!next && sourcePosition) { text.setSelectionRange(sourcePosition.start,sourcePosition.end,sourcePosition.direction); text.scrollTop=sourcePosition.top; text.scrollLeft=sourcePosition.left; }
  }
  setPreview(false); text.oninput = () => { draft.text=text.value; contentHeight=resize(text,240,500)||contentHeight; validate('text'); };
  const pickerRoot = node('div','detail-pickers'); body.append(pickerRoot);
  picker = createValuePickers({root:pickerRoot,idPrefix:'detail-picker',container:$('detail-panel'),maxSelected:{categories:3,keywords:Infinity},allowCreate:true,onRetryCategories:retryCategories,
    onChange:changes=>{ Object.assign(draft,changes); updatePicker(); validate(Object.keys(changes)[0],true); }}); updatePicker();
  for(const [key,title] of [['categories',t('detail.field.categories')],['keywords',t('detail.field.keywords')]]) {
    const section=field('detail-edit-'+key,title,$('detail-picker-'+(key==='categories'?'category':'keyword')+'-control'));
    pickerRoot.append(section);fieldError(section,key,$('detail-picker-'+key));
  }
  const anchors = node('div','detail-anchor-values',undefined,'detail-tags'), input = node('input','detail-anchor-input'); input.placeholder=t('detail.anchor.placeholder'); input.setAttribute('aria-label',t('detail.anchor.inputAria'));
  const add = button('detail-anchor-add',t('detail.anchor.add'),()=>{
    const value = normalizeAnchors([input.value])[0];
    if (!value || Array.from(value).length > 128 || draft.anchors.length >= 5) { input.focus();const error=$('detail-field-error-anchors');error.textContent=t('detail.error.anchors');error.hidden=false;input.setAttribute('aria-invalid','true');return; }
    if (!normalizeAnchors(draft.anchors).some(existing=>existing===value || (/^[\x00-\x7f]*$/.test(existing) && existing.toLowerCase()===value.toLowerCase()))) draft.anchors.push(value); input.value=''; paintAnchors(); validate('anchors',true);
  });
  input.oninput=()=>{const error=$('detail-field-error-anchors');error.textContent='';error.hidden=true;input.setAttribute('aria-invalid','false');};
  input.onkeydown=e=>{ if(e.key==='Enter'&&!e.isComposing){e.preventDefault();add.click();} };
  function paintAnchors() { anchors.replaceChildren(...draft.anchors.map((value,index)=>button('detail-anchor-'+(index+1),String(value)+' ×',()=>{draft.anchors.splice(index,1);paintAnchors();validate('anchors',true);}))); }
  paintAnchors(); const anchorSection = field('detail-edit-anchors',t('detail.field.anchors'),anchors); anchorSection.append(input,add,node('p','detail-anchor-hint',t('detail.anchor.hint'),'text-muted')); fieldError(anchorSection,'anchors',input);body.append(anchorSection);
  if(!creating) body.append(technical());
  let lastWidth = 0;
  function resizeFields() {
    resize($('edit-summary'),64,130);
    if(!preview) contentHeight=resize(text,240,500)||contentHeight;
    else rendered.style.height=contentHeight+'px';
  }
  const observer=new ResizeObserver(entries=>{
    const width=entries[0].contentRect.width;
    if(width!==lastWidth){lastWidth=width;resizeFields();}
  });
  observer.observe(body);document.fonts?.addEventListener('loadingdone',resizeFields);resizeFields();
  validate(); return {...picker,
    update(...args) { picker.update(...args); refreshNamespaces(); },
    isOpen:()=>namespacePicker.isOpen()||picker.isOpen(),
    focusRoot:()=>namespacePicker.focusRoot()||picker.focusRoot(),
    close(restore=false) { if(namespacePicker.isOpen())namespacePicker.close(restore);else picker.close(restore); },
    destroy() { observer.disconnect();document.fonts?.removeEventListener('loadingdone',resizeFields);namespacePicker.destroy();picker.destroy(); },
  };
}
