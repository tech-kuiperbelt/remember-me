// Memories assembly: state/rendering stay in dedicated modules.
import { $ } from './core.js';
import { toast } from './ui.js';
import { getNamespaces, getMaintenance, onNamespacesChanged,
  onMaintenanceChanged, onMemoriesChanged } from './store.js';
import { switchPage } from './router.js';
import { createMemoriesData } from './memories-data.js';
import { createMemoriesList } from './memories-list.js';

let data;
let focusId = null;
const activeRow = () => document.activeElement?.closest('.memory-row')?.dataset.memoryId;
function restoreFocus(id) {
  const row = Array.from($('memories-list').querySelectorAll('.memory-row'))
    .find(node => node.dataset.memoryId === id);
  (row?.querySelector('.memory-open') || row || $('memories-count')).focus();
}

function updateNamespaces() {
  const current = data.snapshot().namespace, select = $('memories-namespace');
  const options = getNamespaces();
  select.replaceChildren(new Option('全部 Namespace', ''),
    ...options.map(ns => new Option(ns, ns)));
  if (current && !options.includes(current)) select.append(new Option(current + '（已不可用）', current));
  select.value = current;
}

function updateClear() {
  $('btn-clear-ns').disabled = !data.snapshot().namespace || getMaintenance()?.state !== 'normal';
}

export function loadMemories() { return data.refresh(); }

export function mount({preselectClearNamespace = () => {}, openMemory = () => {}} = {}) {
  const view = createMemoriesList({root: $('memories-list'), pagination: $('memories-pagination'),
    count: $('memories-count'),
    onOpen: id => openMemory(id, {onClose: () => restoreFocus(id)}),
    onPage: page => data.change({page}, false),
    onPageSize: pageSize => data.change({pageSize}),
    onRetry: () => data.refresh(),
    onAll: () => { data.change({namespace: ''}); updateNamespaces(); }});
  data = createMemoriesData(state => {
    focusId = activeRow() || focusId;
    view.render(state); updateClear();
    if (state.status !== 'loading' && focusId) {
      // Refetch replaces the focused row; restore by identity, not DOM position.
      if (document.activeElement === document.body) restoreFocus(focusId);
      focusId = null;
    }
  });
  $('memories-namespace').addEventListener('change', event => data.change({namespace: event.target.value}));
  $('memories-sort').addEventListener('change', event => data.change({sort: event.target.value}));
  $('btn-clear-ns').addEventListener('click', () => {
    const ns = data.snapshot().namespace;
    if (!ns || getMaintenance()?.state !== 'normal') return;
    switchPage('ops'); preselectClearNamespace(ns);
    toast('Enter maintenance mode, then use Clear in Ops.', 'info');
  });
  onNamespacesChanged(updateNamespaces);
  onMaintenanceChanged(updateClear);
  onMemoriesChanged(() => data.refresh());
  updateNamespaces(); updateClear();
}
