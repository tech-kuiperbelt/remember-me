(async () => {
const result = document.createElement('pre'); result.id = 'smoke-result'; document.body.append(result);
const assert = (condition, text) => {if (!condition) throw new Error(text)};
try {
 await loadStats();
 assert(!$('stats-content').textContent.includes('cache clear'), 'Stats leaked vocabulary');
 switchPage('keywords'); await loadKeywords(true);
 assert($('kw-results').textContent.includes('63 matches'), 'browse count');
 assert(!$('kw-next').disabled, 'next page enabled');
 keywordOffset=50; await loadKeywords();
 assert($('kw-results').textContent.includes('51–63'), 'pagination');
 $('kw-query').value='CACHE'; await loadKeywords(true);
 assert($('kw-results').textContent.includes('2 matches'), 'literal search');
 $('kw-mode').value='semantic'; await loadKeywords(true);
 assert($('kw-results').textContent.includes('score 1.000'), 'semantic scores');
 assert($('kw-next').disabled && $('kw-sort').disabled, 'semantic controls');
 openKeywordMemories('cache', 'a'); await doListQuery();
 assert($('list-ns').value==='a', 'namespace jump');
 assert(Array.from($('list-keywords').selectedOptions).some(o=>o.value==='cache'), 'selected keyword');
 assert($('search-results').textContent.includes('memory cache'), 'memory result');
 await updateKeywordDropdown(); await updateKeywordDropdown(true);
 assert(Array.from($('list-keywords').options).some(o=>o.value==='word059'), 'load more');
 assert(Array.from($('list-keywords').selectedOptions).some(o=>o.value==='cache'), 'selection retained');
 $('list-keyword-query').value='rare_id'; await updateKeywordDropdown();
 assert(Array.from($('list-keywords').options).some(o=>o.value==='rare_id'), 'low frequency word');
 const originalApi=api; let release;
 api=()=>new Promise(resolve=>{release=resolve});
 $('kw-query').value='stale'; const stale=loadKeywords(true);
 api=originalApi; $('kw-query').value='absent'; $('kw-mode').value='literal'; await loadKeywords(true);
 release({results:[{total:1,items:[{keyword:'stale',count:1}]}]}); await stale;
 assert($('kw-results').textContent==='No matching keywords.', 'stale response ignored');

 const special = `部门'"\\<img src=x onerror=window.injected=1>`;
 const specialId = `id'"\\<svg onload=window.injected=1>`;
 allNamespaces = ['a', special];
 statsData.namespace_counts[special] = 1;
 updateSearchNsDropdown(); updateKeywordNamespaces(); updateNsTabs();
 assert(Array.from($('search-ns').options).some(o=>o.value===special), 'namespace option roundtrip');
 const normalLoad = loadMemories;
 let selectedNamespace;
 loadMemories = () => {selectedNamespace = currentNamespace;};
 Array.from($('ns-tabs').children).find(el=>el.dataset.ns===special).click();
 assert(selectedNamespace===special, 'namespace click roundtrip');
 loadMemories = normalLoad;
 const record = {id:specialId, namespace:special, text:special, summary:special,
                 created_at:'2026-09-22T00:00:00Z', updated_at:'2026-09-22T00:00:00Z', score:1, metadata:{}};
 const originals = {viewDetail, editMemory, deleteMemory, saveEdit};
 let action, received;
 viewDetail = id => {action='view';received=id};
 editMemory = id => {action='edit';received=id};
 deleteMemory = id => {action='delete';received=id};
 saveEdit = id => {action='save';received=id};
 renderMemories([record]);
 for (const [index, expected] of [[0,'view'],[1,'edit'],[2,'delete']]) {
   $('memories-list').querySelectorAll('[data-memory-action]')[index].click();
   assert(action===expected && received===specialId, 'memory action '+expected);
 }
 assert($('memories-list').querySelector('.memory-text').title===special, 'title roundtrip');
 renderListResults([record], 'created_at');
 $('search-results').querySelector('button').click();
 assert(action==='view' && received===specialId, 'exact results action');
 renderSearchResults([record]);
 $('search-results').querySelector('button').click();
 assert(action==='view' && received===specialId, 'semantic results action');
 showDetail(record, true);
 assert($('edit-namespace').value===special, 'edit namespace value');
 const option=Array.from($('ns-dropdown').children).find(el=>el.textContent===special);
 $('edit-namespace').value='';option.dispatchEvent(new MouseEvent('mousedown'));
 assert($('edit-namespace').value===special, 'namespace picker');
 Array.from($('detail-actions').children).find(el=>el.textContent==='Save').click();
 assert(action==='save' && received===specialId, 'detail save action');
 showDetail(record, false);
 Array.from($('detail-actions').children).find(el=>el.textContent==='Edit').click();
 assert(action==='edit' && received===specialId, 'detail edit action');
 showDetail(record, false);
 Array.from($('detail-actions').children).find(el=>el.textContent==='Delete').click();
 assert(action==='delete' && received===specialId, 'detail delete action');
 for (const id of ['memories-list','search-results','stats-content']) {
   showError(id, new Error(special));
   assert($(id).textContent==='Error: '+special && !$(id).querySelector('img'), 'literal error '+id);
 }
 viewDetail=originals.viewDetail;editMemory=originals.editMemory;
 deleteMemory=originals.deleteMemory;saveEdit=originals.saveEdit;
 let requested;
 api = async path => {requested=path;return record;};
 await viewDetail(specialId);
 assert(requested==='/memories/'+encodeURIComponent(specialId), 'encoded detail URL');
 api=originalApi;
 await new Promise(resolve=>setTimeout(resolve,100));
 assert(!window.injected && !document.querySelector('img[src="x"]'), 'no injected elements or script');
 result.textContent='PASS: special characters, safe actions and errors;  browse, pagination, literal, semantic, exact jump, selection retention, low-frequency, stale response';
} catch(e) {result.textContent='FAIL: '+e.stack;}
})();
