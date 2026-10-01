// add.js — create a memory from the Add page.

import { $ } from './core.js';
import { api, isNamespaceNotFound, confirmNamespaceCreation } from './api.js';
import { toast } from './ui.js';
import { emitStatsChanged, onMaintenanceChanged } from './store.js';

async function doAdd(e) {
  e.preventDefault();
  const text = $('add-text').value;
  let meta = {};
  try {
    const raw = $('add-metadata').value;
    if (raw.trim()) meta = JSON.parse(raw);
  } catch(e) { toast('Invalid metadata JSON', 'error'); return }
  const ns = $('add-ns').value || 'default';
  try {
    let r;
    try {
      r = await api('/memories', {
        method: 'POST',
        body: JSON.stringify({text, metadata: meta, namespace: ns}),
      });
    } catch(err) {
      if (!isNamespaceNotFound(err) || !confirmNamespaceCreation(err)) throw err;
      r = await api('/memories', {
        method: 'POST',
        body: JSON.stringify({text, metadata: meta, namespace: ns,
                              confirm_codes: ['namespace_not_found']}),
      });
    }
    toast('Memory added: ' + r.id);
    $('add-text').value = '';
    $('add-metadata').value = '';
    emitStatsChanged();
  } catch(e) {
    toast(e.message, 'error');
  }
}

export function mount() {
  $('form-add').addEventListener('submit', doAdd);
  onMaintenanceChanged(state => { $('btn-add-submit').disabled = state.state !== 'normal'; });
}
