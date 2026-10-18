// One drawer's business state. DOM, focus and notifications belong to detail.js.
import { api, isNamespaceNotFound, confirmNamespaceCreation } from './api.js';
import { createDraft, isDirty, validateDraft, buildUpdatePayload, createNewDraft,
  validateCreateDraft, isCreateDirty, buildCreatePayload } from './detail-model.js';
import { t } from './i18n.js';

export function createDetailSession() {
  const cancelled = Symbol('namespace confirmation cancelled');
  const session = {state: 'closed', memory: null, draft: null, selected: null,
    mode: 'detail', initialDraft: null, duplicateId: null,
    version: 0, error: null, writeOutcome: null};
  const current = request => request === session.version && session.state !== 'closed';
  const reset = (mode, id) => {
    ++session.version;
    Object.assign(session, {mode, selected: id, memory: null, draft: null,
      initialDraft: null, duplicateId: null, error: null, writeOutcome: null});
  };
  function open(id) { reset('detail', id); session.state = 'loading'; }
  function create(namespace) {
    reset('create', null);
    session.draft = createNewDraft(namespace);
    session.initialDraft = structuredClone(session.draft);
    session.state = 'edit';
  }
  function close() { ++session.version; session.state = 'closed'; session.selected = null; }
  function edit() { session.writeOutcome = null; session.draft = createDraft(session.memory); session.state = 'edit'; session.error = null; }
  function cancelEdit() { session.draft = null; session.state = 'view'; session.error = null; }
  function useDefaultNamespace(value) { session.draft.namespace = value; session.initialDraft.namespace = value; }
  const hasChanges = () => session.draft && (session.mode === 'create'
    ? isCreateDirty(session.initialDraft, session.draft) : session.memory && isDirty(session.memory, session.draft));
  const errors = () => session.mode === 'create' ? validateCreateDraft(session.draft) : validateDraft(session.draft, session.memory);
  const busy = () => ['saving', 'reloading', 'deleting'].includes(session.state);
  const editing = () => ['edit', 'saving'].includes(session.state);
  const dirty = () => session.state === 'edit' && hasChanges();

  async function read(afterSave = false) {
    const request = ++session.version, id = session.selected;
    session.state = afterSave ? 'reloading' : 'loading'; session.error = null;
    try {
      const memory = await api('/memories/' + encodeURIComponent(id));
      if (!current(request)) return false;
      Object.assign(session, {memory, draft: null, state: 'view'});
    } catch (error) {
      if (!current(request)) return false;
      session.error = error;
      session.state = error.status === 404 ? 'not-found' : afterSave ? 'saved-error' : 'load-error';
    }
    return true;
  }
  async function writeWithConfirmation(path, method, payload, request) {
    try { return await api(path, {method, body: JSON.stringify(payload)}); }
    catch (error) {
      if (!current(request) || !isNamespaceNotFound(error)) throw error;
      if (!confirmNamespaceCreation(error)) return cancelled;
      return api(path, {method, body: JSON.stringify({...payload, confirm_codes: ['namespace_not_found']})});
    }
  }
  async function save() {
    if (session.state !== 'edit' || Object.keys(errors()).length || (session.mode !== 'create' && !hasChanges())) return;
    const creating = session.mode === 'create', request = session.version;
    const payload = creating ? buildCreatePayload(session.draft) : buildUpdatePayload(session.memory, session.draft);
    session.state = 'saving'; session.error = null; session.duplicateId = null; session.writeOutcome = null;
    try {
      const result = await writeWithConfirmation(creating ? '/memories' : '/memories/' + encodeURIComponent(session.selected), creating ? 'POST' : 'PUT', payload, request);
      if (!current(request)) return;
      if (result === cancelled) { session.state = 'edit'; return 'cancelled'; }
      if (creating) {
        if (!result || typeof result.id !== 'string' || typeof result.created !== 'boolean') throw new Error(t('detail.error.createIncomplete'));
        if (!result.created) {
          session.duplicateId = result.id; session.state = 'edit'; session.writeOutcome = 'duplicate';
          return 'duplicate';
        }
        session.mode = 'detail'; session.selected = result.id;
      }
      session.draft = null; session.state = 'reloading'; session.writeOutcome = creating ? 'created' : 'updated';
      return session.writeOutcome;
    } catch (error) {
      if (!current(request)) return;
      session.state = 'edit'; session.error = error;
      return 'error';
    }
  }
  async function remove() {
    if (session.state !== 'view') return;
    const request = session.version, deletedId = session.selected;
    session.state = 'deleting'; session.error = null;
    try {
      await api('/memories/' + encodeURIComponent(deletedId), {method: 'DELETE'});
      if (!current(request)) return;
      session.writeOutcome = 'deleted';
      return deletedId;
    } catch (error) {
      if (!current(request)) return;
      session.state = 'view'; session.error = error;
    }
  }
  return {session, open, create, close, edit, cancelEdit, useDefaultNamespace,
    hasChanges, errors, busy, editing, dirty, read, save, remove};
}
