// Editable namespace field with a full candidate list bounded by the viewport.
import { t } from './i18n.js';
export function createNamespacePicker({control, input, onChoose, onOpen = () => {}, idPrefix = 'detail-namespace', popupRoot = document.getElementById('detail-panel')}) {
  const trigger=document.createElement('button'); trigger.id=idPrefix+'-select';
  trigger.type='button'; trigger.textContent='⌄'; trigger.title=t('picker.namespace.trigger');
  trigger.disabled=true;
  trigger.setAttribute('aria-label',t('picker.namespace.trigger')); trigger.setAttribute('aria-haspopup','listbox');
  trigger.setAttribute('aria-controls',idPrefix+'-options'); trigger.setAttribute('aria-expanded','false');
  control.append(trigger);
  let values=[], popup=null, anchor=null;
  const options=()=>popup ? [...popup.querySelectorAll('[role=option]')] : [];
  function focusOption(option) {
    if(!option)return;
    option.focus({preventScroll:true});
    if(option.offsetTop<popup.scrollTop)popup.scrollTop=option.offsetTop;
    else if(option.offsetTop+option.offsetHeight>popup.scrollTop+popup.clientHeight)
      popup.scrollTop=option.offsetTop+option.offsetHeight-popup.clientHeight;
  }
  function close(restore=false) {
    if(!popup)return;
    popup.remove(); popup=null; anchor=null; trigger.setAttribute('aria-expanded','false');
    if(restore)trigger.focus();
  }
  function paint() {
    if(!popup)return;
    const focused=document.activeElement?.dataset.namespace;
    popup.replaceChildren(...values.map((value,index)=>{
      const option=document.createElement('button'); option.type='button';
      option.id=idPrefix+'-option-'+(index+1); option.dataset.namespace=value;
      option.textContent=value; option.title=value; option.setAttribute('role','option');
      option.setAttribute('aria-selected',String(value===input.value)); option.tabIndex=-1;
      option.onclick=()=>{close();onChoose(value);input.focus();};
      return option;
    }));
    const selected=options().find(option=>option.dataset.namespace===(focused || input.value)) || options()[0];
    if(selected)selected.tabIndex=0;
    if(focused)focusOption(selected);
  }
  function open(last=false) {
    if(trigger.disabled||!values.length)return;
    onOpen(); close();
    popup=document.createElement('div'); popup.id=idPrefix+'-options';
    popup.className='detail-namespace-options'; popup.setAttribute('role','listbox');
    popup.setAttribute('aria-label',t('picker.namespace.list'));
    popupRoot.append(popup); paint();
    const box=control.getBoundingClientRect(), margin=8;
    anchor=box;
    const width=Math.min(box.width,innerWidth-2*margin);
    popup.style.width=width+'px';
    popup.style.left=Math.max(margin,Math.min(box.left,innerWidth-width-margin))+'px';
    const below=innerHeight-box.bottom-margin-4, above=box.top-margin-4;
    const down=below>=Math.min(280,popup.scrollHeight)||below>=above;
    popup.style.maxHeight=Math.max(0,Math.min(280,down?below:above))+'px';
    if(down)popup.style.top=Math.max(margin,box.bottom+4)+'px';
    else popup.style.bottom=Math.max(margin,innerHeight-box.top+4)+'px';
    trigger.setAttribute('aria-expanded','true');
    const focused=last ? options().at(-1) : options().find(option=>option.tabIndex===0);
    options().forEach(option=>option.tabIndex=option===focused?0:-1);focusOption(focused);
    popup.onkeydown=event=>{
      if(event.isComposing)return;
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);return;}
      const list=options(), index=list.indexOf(document.activeElement); let next;
      if(event.key==='ArrowDown')next=(index+1)%list.length;
      else if(event.key==='ArrowUp')next=(index+list.length-1)%list.length;
      else if(event.key==='Home')next=0;
      else if(event.key==='End')next=list.length-1;
      else return;
      event.preventDefault();list.forEach((option,i)=>option.tabIndex=i===next?0:-1);focusOption(list[next]);
    };
  }
  trigger.onclick=()=>popup?close():open();
  const onKey=event=>{
    if(!event.isComposing&&(event.key==='ArrowDown'||event.key==='ArrowUp')) {
      event.preventDefault();open(event.key==='ArrowUp');
    }
  };
  trigger.addEventListener('keydown',onKey); input.addEventListener('keydown',onKey);
  const outside=event=>{if(popup&&!popup.contains(event.target)&&!control.contains(event.target))close();};
  const scroll=event=>{
    if(!popup||popup.contains(event.target))return;
    const box=control.getBoundingClientRect();
    if(box.top!==anchor.top||box.left!==anchor.left)close();
  };
  const resize=()=>close();
  document.addEventListener('click',outside);document.addEventListener('scroll',scroll,true);
  window.addEventListener('resize',resize);
  return {
    update(next) {
      if(JSON.stringify(next)===JSON.stringify(values))return;
      values=next; trigger.disabled=!values.length; if(!values.length)close();else paint();
    },
    isOpen:()=>Boolean(popup), focusRoot:()=>popup, close,
    destroy() {
      close();document.removeEventListener('click',outside);document.removeEventListener('scroll',scroll,true);
      window.removeEventListener('resize',resize);input.removeEventListener('keydown',onKey);
    },
  };
}
