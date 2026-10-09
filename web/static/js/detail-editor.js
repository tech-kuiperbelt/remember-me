// Shared create/update form; the drawer owns requests, dirty state and focus.
import { $ } from './core.js';
import { getNamespaces, getDefaultNamespace, getCategories, getCategoryState } from './store.js';
import { normalizeAnchors } from './detail-model.js';
import { renderMarkdown } from './markdown.js';
import { createValuePickers } from './memory-value-picker.js';
import { createNamespacePicker } from './namespace-picker.js';
const node = (tag, id, text, cls = '') => { const el = document.createElement(tag); if (id) el.id = id; if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; };
function button(id, text, action, cls = 'btn-outline') { const el=node('button',id,text,'btn btn-sm '+cls);el.type='button';el.onclick=action;return el; }
function field(id,title,content) {const section=node('section',id,undefined,'detail-section');section.append(node('h2',id+'-label',title),content);return section;}
export function renderDetailEditor({body, draft, creating, validate, showError, technical, retryCategories, onRetryDefault}) {
  let picker;
  let namespacePicker;
  let preview = false;
  let refreshNamespaces = () => {};
  const updatePicker = () => picker?.update(draft,{namespaces:getNamespaces(),categories:getCategories(),categoryState:getCategoryState()});
  const status = node('p','detail-error','', 'text-danger'); status.hidden = true; status.setAttribute('role','alert'); body.append(status);
  for (const [key,label] of [['namespace','Namespace'],['summary','Summary']]) {
    const input = node(key === 'namespace' ? 'input' : 'textarea','edit-'+key); input.value = draft[key];
    const section = field('detail-edit-'+key,label,input); section.querySelector('h2').replaceWith(Object.assign(node('label','detail-edit-'+key+'-label',label),{htmlFor:input.id}));
    input.oninput = () => { draft[key] = input.value; if(key === 'namespace') {namespacePicker?.close();input.dataset.touched='true';updatePicker();} validate(); }; body.append(section);
    if(key === 'summary' && creating) section.append(node('p','detail-summary-hint','留空将从正文生成摘要。','text-muted'));
    if(key === 'namespace' && creating) {
      const hint=node('p','detail-default-namespace-status','','text-muted'); hint.setAttribute('role','status'); section.append(hint,button('detail-default-namespace-retry','Retry default Namespace',onRetryDefault));
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
  const text = node('textarea','edit-text'); text.value = draft.text; text.spellcheck = false; text.setAttribute('aria-label','Content Markdown source');
  const rendered = node('div','detail-preview',undefined,'detail-markdown'); rendered.hidden = true;
  const modes = node('div','detail-content-modes');
  modes.setAttribute('role','tablist'); modes.setAttribute('aria-label','Content view');
  const editButton = button('detail-source-mode','Edit',()=>setPreview(false)); editButton.className='detail-content-tab';
  const previewButton = button('detail-preview-mode','Preview',()=>setPreview(true)); previewButton.className='detail-content-tab';
  const source = node('div','detail-source-panel'); source.append(text);
  for (const [tab,panel] of [[editButton,source],[previewButton,rendered]]) {
    tab.setAttribute('role','tab'); tab.setAttribute('aria-controls',panel.id);
    panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby',tab.id);
  }
  rendered.tabIndex=0;
  modes.append(editButton,previewButton);
  const editor = node('div','detail-content-editor',undefined,'detail-content-editor'); editor.append(modes,source,rendered);
  body.append(field('detail-edit-content','Content',editor));
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
    preview = next; if(next) renderMarkdown(rendered,draft.text); source.hidden=next; text.hidden=next; rendered.hidden=!next;
    editButton.setAttribute('aria-selected',String(!next)); previewButton.setAttribute('aria-selected',String(next));
    editButton.tabIndex=next?-1:0; previewButton.tabIndex=next?0:-1;
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
  paintAnchors(); const anchorSection = field('detail-edit-anchors','Anchors',anchors); anchorSection.append(input,add,node('p','detail-anchor-hint','修改正文后，请检查锚点是否仍然准确。','text-muted')); body.append(anchorSection);
  if(creating) {
    const extra=node('details','detail-extra-metadata',undefined,'detail-technical');
    const toggle=node('summary','detail-extra-metadata-toggle','其他 metadata'); toggle.dataset.debugNavigation=''; extra.append(toggle);
    const input=node('textarea','detail-extra-metadata-input'); input.value=draft.extraMetadata; input.setAttribute('aria-label','Other metadata JSON');
    input.oninput=()=>{draft.extraMetadata=input.value;validate();};
    extra.append(node('p','detail-extra-metadata-hint','JSON 对象；分类、关键词和锚点请使用上方字段。','text-muted'),input); body.append(extra);
  } else body.append(technical());
  validate(); return {...picker,
    update(...args) { picker.update(...args); refreshNamespaces(); },
    isOpen:()=>namespacePicker.isOpen()||picker.isOpen(),
    focusRoot:()=>namespacePicker.focusRoot()||picker.focusRoot(),
    close(restore=false) { if(namespacePicker.isOpen())namespacePicker.close(restore);else picker.close(restore); },
    destroy() { namespacePicker.destroy();picker.destroy(); },
  };
}
