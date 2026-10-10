// Memories assembly: state/rendering stay in dedicated modules.
import { $ } from './core.js';
import { getNamespaces, getCategories, getCategoryState, getMaintenance, onNamespacesChanged,
  onCategoriesChanged, onMaintenanceChanged, onMemoriesChanged } from './store.js';
import { createMemoriesData } from './memories-data.js';
import { createMemoriesList } from './memories-list.js';
import { createMemoriesSearch } from './memories-search.js';
import { createMemoriesFilters } from './memories-filters.js';
import { createMemoriesSearchLayout } from './memories-search-layout.js';

let data;
let filters;
let searchControls;
let view;
let focusId = null;
const activeRow = () => document.activeElement?.closest('.memory-row')?.dataset.memoryId;
function restoreFocus(id) {
  const row = Array.from($('memories-list').querySelectorAll('.memory-row'))
    .find(node => node.dataset.memoryId === id);
  (row?.querySelector('.memory-open') || row || $('memories-count')).focus();
}

function updateFilters(state = data.snapshot()) {
  filters.update(state, {namespaces: getNamespaces(), categories: getCategories(), categoryState: getCategoryState()});
}

function updateCreate() {
  $('memories-create').disabled = getMaintenance()?.state !== 'normal';
}

export function loadMemories() { return data.refresh(); }
export function hide() { filters.close(); focusId = null; }
export function prepareKeywordBrowse(keyword, namespace) {
  hide(); searchControls.clearDates(); data.prepareKeywordBrowse(keyword, namespace);
}
export function prepareMetadataDrilldown(dimension, value, namespace) {
  hide(); searchControls.clearDates();
  data.prepareMetadataDrilldown(dimension, value, namespace);
  if (dimension === 'anchors')searchControls.resetAnchorDraft();
}
function resetFilters() {
  filters.close();
  searchControls.clearDates(); data.resetFilters();
  $('memories-namespace').focus();
}

export function mount({openMemory = () => {}, openCreate = () => {}, retryCategories = () => {}, isDetailOpen = () => false, onDetailSelection = () => {}} = {}) {
  filters = createMemoriesFilters({root: $('memories-filters'),
    onChange: changes => data.change(changes), onReset: resetFilters, onRetryCategories: retryCategories});
  createMemoriesSearchLayout({bar: document.querySelector('.memories-search-bar'),
    onLayout: () => filters.reposition()});
  searchControls = createMemoriesSearch({onInput: value => data.input(value),
    onMode: mode => data.setMode(mode),
    onSubmit: options => data.submit(options), onClear: () => data.clearSearch(), onApply: options => data.change(options)});
  view = createMemoriesList({root: $('memories-list'), pagination: $('memories-pagination'),
    count: $('memories-count'),
    onOpen: id => { filters.close(); openMemory(id, {onClose: () => restoreFocus(id)}); },
    onPage: page => data.change({page}, false),
    onPageSize: pageSize => data.change({pageSize}),
    onRetry: () => data.refresh(),
    onAll: () => data.change({namespace: ''}), onReset: resetFilters, onClearSearch: () => { data.clearSearch(); $('memories-query').focus(); }});
  onDetailSelection(id => view.markRecent(id));
  let savedScroll = null;
  data = createMemoriesData(state => {
    focusId = activeRow() || focusId;
    const content = $('content');
    if (state.status === 'loading' && savedScroll === null) { savedScroll = content.scrollTop; $('memories-list').style.minHeight = $('memories-list').offsetHeight + 'px'; }
    view.render(state); updateFilters(state); searchControls.update(state); updateCreate();
    const sort = $('memories-sort'), searching = Boolean(state.submittedQuery);
    const options = searching ? [['relevance', 'Relevance']]
      : [['updated_at', 'Updated time ↓'], ['created_at', 'Created time ↓']];
    if (sort.dataset.mode !== String(searching)) {
      sort.replaceChildren(...options.map(([value, label]) => new Option(label, value))); sort.dataset.mode = String(searching);
    }
    sort.value = state.sort; sort.disabled = searching;
    if (state.status !== 'loading' && savedScroll !== null) { $('memories-list').style.minHeight = ''; content.scrollTop = savedScroll; savedScroll = null; }
    if (state.status !== 'loading' && focusId && $('page-memories').classList.contains('active')) {
      // Refetch replaces the focused row; restore by identity, not DOM position.
      if (!isDetailOpen() && document.activeElement === document.body) restoreFocus(focusId);
      focusId = null;
    }
  });
  $('memories-create').onclick = () => {
    if(getMaintenance()?.state !== 'normal')return;
    filters.close();
    openCreate(data.snapshot().namespace,{onClose:()=> $('memories-create').focus()});
  };
  $('memories-sort').addEventListener('change', event => data.change({sort: event.target.value}));
  onNamespacesChanged(() => updateFilters());
  onCategoriesChanged(() => updateFilters());
  onMaintenanceChanged(updateCreate);
  onMemoriesChanged(change => { if (change?.deletedId) view.clearRecent(change.deletedId); data.refresh(); });
  updateFilters(); updateCreate();
}
