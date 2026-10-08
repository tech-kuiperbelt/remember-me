# Memory Detail Drawer — Interaction Specification

## 1. Core state model

Recommended high-level states:

```text
CLOSED
  ↓ select memory
VIEW
  ↓ Edit
EDIT_CLEAN
  ↓ change
EDIT_DIRTY
  ↓ Save
SAVING
  ↓ success
VIEW
```

Additional flows:

```text
EDIT_DIRTY
  ↓ attempt to leave
DISCARD_CONFIRMATION
  ├─ Keep editing → EDIT_DIRTY
  └─ Discard changes → requested destination
```

```text
VIEW
  ↓ Delete
DELETE_CONFIRMATION
  ↓ confirm
DELETING
  ↓ success
CLOSED + remove list item
```

---

## 2. Opening a memory

When a list row is clicked:

1. Open the right-side Drawer
2. Load/show that memory
3. Highlight the row
4. Do not reset search/filter/sort/pagination
5. Do not change list width

If another memory is clicked while the Drawer is already open:

- In View mode: replace Drawer content with the new memory
- In Edit Clean mode: switching is allowed directly
- In Edit Dirty mode: show unsaved-change confirmation first

---

## 3. Dirty-state definition

Dirty state is based on semantic difference from the values captured when Edit mode began.

Fields participating in dirty checking:

- Namespace
- Summary
- Content
- Categories
- Keywords

If the user changes a field and then restores its original value, that field must no longer make the form dirty.

For collection fields, compare normalized values according to API semantics rather than UI ordering unless ordering itself is meaningful.

---

## 4. Save behavior

When Save is clicked:

1. Validate form values
2. Disable editing controls to prevent duplicate submission
3. Submit all changed editable data as required by the API contract
4. Wait for server response
5. On success, treat the server response as authoritative
6. Update Drawer data
7. Return to View mode
8. Update the corresponding row in the current list if it still belongs there
9. Re-evaluate current query + filters

### Result-set reconciliation

After Save, the memory may no longer match current filters because Namespace, Categories, or Keywords may have changed.

Rule:

```text
still matches current query + filters
  → update row in place

no longer matches current query + filters
  → remove row from current result list
```

Do not leave stale data in the current result set.

If the current Search API ranking/score cannot be reproduced safely client-side, prefer re-fetching/revalidating the active result set rather than inventing client-side ranking behavior.

---

## 5. Save success feedback

Show lightweight success feedback such as a toast:

```text
Memory updated
Your changes have been saved.
```

If it was removed from the current filtered result set, optional message:

```text
Memory updated
It no longer matches the current filters and was removed from the list.
```

Do not treat removal from the filtered list as a deletion.

---

## 6. Save failure

If the update request fails:

- Stay in Edit mode
- Preserve all unsaved values
- Re-enable controls
- Show an error message
- Do not mutate/remove the list row based on an unsuccessful save

The user must be able to retry or cancel.

---

## 7. Cancel behavior

Cancel means:

- Discard all unsaved edits from the current edit session
- Restore the last server-confirmed memory values
- Return to View mode

Cancel from Edit Dirty does not require an additional confirmation because the user explicitly chose Cancel.

If desired later, this can be revisited, but the initial implementation should keep the flow simple.

---

## 8. Leaving with unsaved changes

The following actions count as attempts to leave Edit Dirty state:

- Close button
- Esc
- Overlay click
- Selecting a different memory
- Navigation away from Memories if the Drawer component owns that transition

Show one common confirmation dialog:

```text
Discard unsaved changes?

You have unsaved changes in this memory.

[ Keep editing ] [ Discard changes ]
```

### Keep editing

- Close dialog
- Remain in current Drawer/Edit state
- Preserve edits

### Discard changes

- Drop local edits
- Perform the originally requested action

The requested action should be remembered while the confirmation is open.

---

## 9. Delete behavior

Delete is available from View mode via the `...` menu.

Recommended initial rule: do not expose Delete while actively editing. The user should cancel/save first, which keeps destructive behavior unambiguous.

Flow:

1. Open `...`
2. Select `Delete memory`
3. Show confirmation
4. Confirm Delete
5. Disable confirmation actions during request
6. Call Delete API
7. On success:
   - Remove memory from current List/Search result
   - Close Drawer
   - Clear selection
   - Show success toast

---

## 10. Delete failure

If delete fails:

- Keep Drawer open
- Keep the memory in the list
- Close or retain the confirmation dialog according to component convention, but clearly show the failure
- Never optimistically remove the item unless rollback behavior is explicitly implemented

The simplest recommended behavior is server-confirmed deletion before mutating the list.

---

## 11. Markdown Preview behavior

Preview renders the current local Content value.

Rules:

- No API call is required for ordinary client-side Markdown preview
- Preview does not save
- Preview does not clear dirty state
- Edit → Preview → Edit must preserve exact Markdown source
- If render fails, show an inline preview error without losing source text

---

## 12. Closing behavior in View mode

In View mode, these actions close directly:

- Close button
- Esc
- Overlay click

No confirmation is required because there are no unsaved edits.

---

## 13. Keyboard behavior

Recommended:

- `Esc`
  - View → close Drawer
  - Edit Clean → close or return to View according to chosen component behavior; recommended: close Drawer
  - Edit Dirty → unsaved-change confirmation
- Do not bind `Ctrl/Cmd+S` until browser/default handling and accessibility are intentionally designed

Keep the first implementation conservative.
