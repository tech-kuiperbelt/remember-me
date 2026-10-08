# Remember Me — Memory Detail Drawer UI Package

This package documents the agreed UI and interaction design for the Memory Detail experience in Remember Me.

## Scope

This package covers:

- Right-side overlay Drawer as the Detail surface
- View mode
- Edit mode
- Markdown rendering in View mode
- Markdown source editing in Edit mode
- Edit / Preview toggle for Markdown content
- Editable Namespace, Summary, Content, Categories, Keywords
- Read-only ID / Created At / Updated At
- Dirty-state detection
- Save / Cancel behavior
- Unsaved-change confirmation
- Delete flow and confirmation
- Synchronization with the current Memories List/Search result after save/delete

This package intentionally does **not** redesign the Memories List/Search page. That is handled by the previous Memories UI package.

## Files

- `UI_MEMORY_DETAIL_SPEC.md` — visual and information architecture specification
- `UI_MEMORY_DETAIL_INTERACTIONS.md` — behavioral rules and state transitions
- `UI_MEMORY_DETAIL_IMPLEMENTATION_PLAN.md` — suggested implementation sequence
- `images/detail-drawer-view-edit-delete.png` — primary detail design reference
- `images/drawer-vs-panel-reference.png` — reference for choosing overlay Drawer instead of split Panel
- `images/memories-list-context.png` — contextual reference showing where Detail is opened from

## Core decisions

1. Memory Detail uses a **right-side overlay Drawer**, not a split panel and not a separate page.
2. Opening Detail must not resize the Memories List layout.
3. View mode is the default state.
4. Markdown content is rendered in View mode.
5. Edit mode edits raw Markdown source.
6. Markdown preview uses an **Edit / Preview toggle**, not a two-column editor.
7. Namespace, Summary, Content, Categories, and Keywords are editable.
8. ID, Created At, and Updated At are read-only.
9. Edit is explicit; there is no autosave.
10. Delete lives under the `...` menu and requires explicit confirmation.
11. Unsaved changes must be protected when closing the Drawer, pressing Esc, clicking the overlay, or switching to another memory.
12. After save, the memory must be re-evaluated against the active query + filters. If it no longer belongs in the current result set, remove it from the list.
