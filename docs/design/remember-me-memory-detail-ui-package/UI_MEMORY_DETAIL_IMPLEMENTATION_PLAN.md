# Memory Detail Drawer — Suggested Implementation Plan

The implementation should be incremental so View/Edit/Delete behavior is testable independently.

## Phase 1 — Drawer shell and selection

Goal: open/close a stable overlay Drawer without editing.

Implement:

- `selectedMemoryId`
- Drawer open/closed state
- Overlay behavior
- Close button
- Esc close
- Overlay-click close
- Independent Drawer scrolling
- Selected-row highlight
- Responsive Drawer width

Acceptance:

- Opening Detail does not resize the list
- Search/filter/list state remains intact
- List scroll position remains intact

---

## Phase 2 — View mode

Implement the information hierarchy:

- Header
- Namespace + updated time
- Summary
- Markdown-rendered Content
- Categories
- Keywords
- Technical details

Prefer reusable components:

- `MemoryDetailDrawer`
- `MemoryDetailView`
- `MarkdownContent`
- `TagChips`
- `TechnicalDetails`

Acceptance:

- Markdown headings/lists/code/tables render correctly
- Very long content scrolls only inside Drawer
- Many keywords do not destroy layout

---

## Phase 3 — Markdown renderer safety/style

Configure Markdown behavior explicitly.

Implement/test:

- Raw HTML policy
- Sanitization
- Safe links
- Code blocks
- Tables
- Long lines / URLs
- Drawer-specific typography

Do not rely on Markdown library defaults as the visual specification.

---

## Phase 4 — Edit shell

Add View → Edit transition.

Introduce a local edit model copied from the last server-confirmed memory.

Suggested state shape:

```text
mode: view | edit
previewMode: edit | preview
originalMemory
editDraft
isDirty
isSaving
```

Implement editable fields:

- Namespace
- Summary
- Content
- Categories
- Keywords

Keep ID/Created/Updated read-only.

---

## Phase 5 — Shared picker reuse

Reuse the components/behavior already built for the Memories filter where practical:

- Namespace selector
- Category multi-select
- Keyword remote search-to-add picker

Avoid creating a second, inconsistent Categories/Keywords interaction model just for Detail.

Differences in context are acceptable, but selection semantics should stay the same.

---

## Phase 6 — Markdown Edit / Preview toggle

Implement:

```text
[ Edit | Preview ]
```

Default to Edit when entering edit mode.

Preview renders `editDraft.content`, not the last saved content.

Acceptance:

- Toggle causes no data loss
- Preview never triggers save
- Dirty state remains correct

---

## Phase 7 — Dirty state and Cancel

Implement field-level or whole-form normalized comparison.

Requirements:

- Save disabled when clean
- Reverting changes restores clean state
- Cancel restores original server-confirmed values
- Cancel returns to View mode

Test changes to:

- Scalar fields
- Namespace
- Category additions/removals
- Keyword additions/removals
- Markdown text

---

## Phase 8 — Unsaved-change guard

Create one reusable unsaved-change guard for:

- Close button
- Esc
- Overlay click
- Selecting another memory

The guard should capture the intended next action.

Pseudo-flow:

```text
requestLeave(action)
  if !dirty:
      action()
  else:
      pendingAction = action
      showDiscardDialog = true
```

On `Discard changes`, execute `pendingAction`.

---

## Phase 9 — Save API integration

Implement server update.

On success:

1. Replace original memory with server response
2. Clear dirty state
3. Return to View mode
4. Synchronize list row
5. Reconcile active query + filters

Important:

If a saved memory no longer matches current filters, remove it from the current list.

For semantic Search results, if score/ranking cannot be correctly recalculated on the client, refresh/revalidate the search result rather than guessing.

On failure:

- Preserve draft
- Remain in Edit mode
- Show error

---

## Phase 10 — Delete

Implement View-mode `...` menu.

Add:

- Delete menu item
- Confirmation dialog
- Delete request state
- Server-confirmed removal
- Drawer close
- List removal
- Success/error feedback

Do not expose destructive delete as a primary button.

---

## Phase 11 — Responsive behavior

Recommended behavior:

Desktop:

- Right-side overlay Drawer, approx. 560–640px

Medium screens:

- Increase Drawer percentage width

Small screens:

- Drawer becomes near/full-width
- Maintain same View/Edit interaction model

Do not create a separate mobile Detail information architecture unless later usability testing requires it.

---

## Phase 12 — Test matrix

### View

- Open detail from List
- Open detail from Search result
- Long Markdown
- Code block
- Table
- Many keywords
- Missing optional values

### Edit

- Change only Summary
- Change Markdown
- Preview unsaved Markdown
- Change Namespace
- Add/remove Category
- Add/remove Keyword
- Revert every change to original

### Leave protection

- Close button while dirty
- Esc while dirty
- Overlay click while dirty
- Select another memory while dirty

### Save

- Successful save still matches filters
- Successful save no longer matches filters
- Save failure
- Search result revalidation

### Delete

- Cancel delete
- Successful delete
- Delete failure

---

## Recommended implementation order summary

```text
Drawer shell
→ View mode
→ Markdown rendering
→ Edit form
→ Shared pickers
→ Edit/Preview toggle
→ Dirty/Cancel
→ Unsaved guard
→ Save/reconcile
→ Delete
→ Responsive polish
```

Do not implement every interaction in one pass. Keep each phase independently reviewable.
