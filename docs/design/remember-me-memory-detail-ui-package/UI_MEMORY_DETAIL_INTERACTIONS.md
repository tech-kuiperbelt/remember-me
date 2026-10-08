# Memory Detail Drawer — Interaction Specification

Updated 2026-10-08 to match implementation. See [50.6.2 behavior contract](../../project-management/plans/50.6.2-memory-detail-frontend.md) and [50.6.3 implementation record](../../project-management/plans/50.6.3-memory-detail-coding-plan.md).

## Session and states

The drawer owns selected ID, last confirmed memory, draft, preview mode, request version and one pending leave action. States are CLOSED, LOADING, LOAD_ERROR, VIEW, EDIT (clean/dirty derived), SAVING, RELOADING_AFTER_SAVE, SAVED_RELOAD_ERROR, NOT_FOUND and DELETING. Menus, pickers and confirmations are nested states.

Opening shows the modal immediately, focuses Close, loads the record and selects its row by ID. Background content is inert; users close before clicking another row. Programmatic target changes remain possible through the leave guard. Each open, close and reread invalidates earlier reads; late A/B or closed-session responses cannot replace or reopen the drawer.

## Draft and editing

Edit copies the last confirmed record and focuses Namespace. Content compares exact source; Namespace/Summary compare trimmed values, with null summary and blank input equivalent. Categories/Keywords compare normalized sets; Anchors compare normalized ordered arrays. Reverting values restores clean state.

Save requires dirty and valid values. Untouched historical dimensions are not cleaned or truncated. An explicitly edited invalid dimension must be repaired. Preview only renders draft Content, retaining source, selection, scroll and dirty state.

Cancel explicitly discards the draft and returns to the last confirmed View; it does not close the drawer or require a second confirmation. Copy content copies saved source in View; Copy draft copies unsaved source in Edit. Clipboard failures are reported.

## Leaving and focus

Close, overlay, Escape, programmatic record changes and router navigation call one synchronous `requestLeave(action)` coordinator. Clean sessions leave directly. Dirty sessions capture the first action and show Keep editing / Discard changes, initially focusing Keep editing. Later requests cannot replace the pending destination.

Keep editing/Escape dismisses the confirmation and retains the draft. Discard executes the original continuation once. Navigation preparation is inside that continuation, so cancelling cannot mutate destination filters early. Dirty and busy states register beforeunload protection; no persistent draft or autosave exists.

Saving/deleting rejects leave and repeated business actions. Tab cycles within the topmost confirmation/picker/menu/drawer. Closed details' internal controls are excluded. Escape first exits Debug, or closes the topmost nested surface, before attempting drawer leave. IME Escape is ignored. Closing restores row focus, falling back to the result bar if necessary.

## Save contract and failures

1. Validate and build a minimal update: changed scalars and changed metadata dimensions only.
2. Body changes additionally send current Summary and Anchors, including `summary: ""` and `anchors: []`. This deliberately keeps stored null summary null after body edits, while list/LLM display fallback remains available. HTTP null/omission is not the clear operation; the frontend never sends null to clear.
3. Lock the form and send PUT once. Unknown Namespace uses existing confirmation and a confirmed retry; cancelling returns to the same draft.
4. PUT failure retains draft and re-enables controls with an inline error. PUT 404 reports the missing record while preserving Copy draft.
5. PUT success clears dirty and emits one Memories and one Stats notification, then GETs the authoritative record. PUT returns a receipt, not detail data.
6. GET success returns to View with the drawer open. GET failure enters read-only SAVED_RELOAD_ERROR; Retry repeats GET only. GET 404 enters NOT_FOUND.

Successful writes are not reported as failed because a subsequent detail or list read failed. List errors are displayed by the list independently.

## Result refresh and selection

The data module re-runs the applied Browse/Semantic/Lexical/Anchor request after save/delete. It retains query/parameter drafts, applied filters, sort, page size and page, correcting an out-of-range final page. It owns ranking and membership; the drawer does not edit rows or scores optimistically.

Refresh retains sufficient list height and restores/clamps scroll after completion. It does not steal drawer focus. Selection stays tied to ID through reorder or disappearance; it never transfers to a different row at the same position. An absent record on the current page does not justify an “exited filters” message. Clearing Summary bumps updated_at and may change normal Browse order/time-filter membership.

## Delete

Delete exists only in View's More menu. Confirmation includes the title and full ID and defaults to Cancel. Cancel retains View. Confirm locks actions and issues one DELETE.

Success closes, clears selection, emits one Memories/Stats refresh and shows success. Failure retains confirmation and record, displays the error, and enables Retry/Cancel. DELETE 404 says the record is already missing; the user can close and refresh to reconcile. No optimistic deletion occurs.

## Deferred behavior

There is no autosave, persisted draft, version-conflict detection, Ctrl/Cmd+S binding or new backend endpoint. PUT→GET retains the existing concurrent-write window; the GET is authoritative for display.
