// Shared, object-specific 8-character confirmation for Clear and Recover.
import { $ } from './core.js';
let pending=null;
export function confirmDestructive({title,scope,action}) {
  if(pending)return Promise.resolve(false);
  const chars='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  const code=Array.from(crypto.getRandomValues(new Uint32Array(8)),n=>chars[n%chars.length]).join('');
  const modal=$('clear-modal'), input=$('clear-confirm-input'), accept=$('btn-clear-confirm');
  const prior=document.activeElement, shell=$('app-shell'), wasInert=shell.inert;
  $('clear-confirm-title').textContent=title;
  $('clear-confirm-scope').textContent=scope+'。此操作不可撤销，请准确输入下方8位确认码。';
  $('clear-confirm-code').textContent=code; input.value=''; accept.disabled=true; accept.textContent=action;
  modal.style.display='flex';shell.inert=true;input.focus();
  return new Promise(resolve=>{
    function close(ok) {
      modal.style.display='none';shell.inert=wasInert;document.removeEventListener('keydown',keydown,true);
      input.oninput=null;accept.onclick=null;$('btn-clear-cancel').onclick=null;modal.onclick=null;
      pending=null;if(prior?.isConnected)prior.focus();resolve(ok);
    }
    function keydown(e) {
      if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();close(false);}
      if(e.key==='Enter'&&e.target===input){e.preventDefault();if(!accept.disabled)close(true);}
      if(e.key==='Tab') {
        const nodes=[input,$('btn-clear-cancel'),...(!accept.disabled?[accept]:[])];
        const i=nodes.indexOf(document.activeElement);
        e.preventDefault();nodes[(i+(e.shiftKey?-1:1)+nodes.length)%nodes.length].focus();
      }
    }
    pending=close;
    input.oninput=()=>{accept.disabled=input.value!==code;};
    accept.onclick=()=>{if(!accept.disabled)close(true);};
    $('btn-clear-cancel').onclick=()=>close(false);
    modal.onclick=e=>{if(e.target===modal)close(false);};
    document.addEventListener('keydown',keydown,true);
  });
}
