// Memories assembly: state/rendering stay in dedicated modules.
import { $ } from './core.js';
import { toast } from './ui.js';
import { getNamespaces, getCategories, getCategoryState, getMaintenance, onNamespacesChanged,
  onCategoriesChanged, onMaintenanceChanged, onMemoriesChanged } from './store.js';
import { switchPage } from './router.js';
import { createMemoriesData } from './memories-data.js';
import { createMemoriesList } from './memories-list.js';
import { createMemoriesFilters } from './memories-filters.js';

let data;
let filters;
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

function updateClear() {
  $('btn-clear-ns').disabled = !data.snapshot().namespace || getMaintenance()?.state !== 'normal';
}

export function loadMemories() { return data.refresh(); }
export function hide() { filters.close(); }
function resetFilters() {
  filters.close();
  data.resetFilters();
  $('memories-namespace').focus();
}

export function mount({preselectClearNamespace = () => {}, openMemory = () => {}, retryCategories = () => {}} = {}) {
  filters = createMemoriesFilters({root: $('memories-filters'),
    onChange: changes => data.change(changes), onReset: resetFilters, onRetryCategories: retryCategories});
  const view = createMemoriesList({root: $('memories-list'), pagination: $('memories-pagination'),
    count: $('memories-count'),
    onOpen: id => openMemory(id, {onClose: () => restoreFocus(id)}),
    onPage: page => data.change({page}, false),
    onPageSize: pageSize => data.change({pageSize}),
    onRetry: () => data.refresh(),
    onAll: () => data.change({namespace: ''}), onReset: resetFilters});
  data = createMemoriesData(state => {
    focusId = activeRow() || focusId;
    view.render(state); updateFilters(state); updateClear();
    if (state.status !== 'loading' && focusId) {
      // Refetch replaces the focused row; restore by identity, not DOM position.
      if (document.activeElement === document.body) restoreFocus(focusId);
      focusId = null;
    }
  });
  $('memories-sort').addEventListener('change', event => data.change({sort: event.target.value}));
  $('btn-clear-ns').addEventListener('click', () => {
    const ns = data.snapshot().namespace;
    if (!ns || getMaintenance()?.state !== 'normal') return;
    switchPage('ops'); preselectClearNamespace(ns);
    toast('Enter maintenance mode, then use Clear in Ops.', 'info');
  });
  onNamespacesChanged(() => updateFilters());
  onCategoriesChanged(() => updateFilters());
  onMaintenanceChanged(updateClear);
  onMemoriesChanged(() => data.refresh());
  updateFilters(); updateClear();
}
