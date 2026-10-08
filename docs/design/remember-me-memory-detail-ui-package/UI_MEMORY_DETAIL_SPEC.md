# Memory Detail Drawer — UI Specification

## 1. Purpose

The Memory Detail surface is responsible for three primary tasks:

1. Viewing a memory
2. Editing a memory
3. Deleting a memory

It must remain secondary to the Memories workspace. The user's browsing/search context must stay visible and preserved.

---

## 2. Surface choice: right-side overlay Drawer

Use a right-side overlay Drawer.

### Why

- Keeps the Memories List as the main workspace
- Preserves active search, filters, pagination, sort, and scroll position
- Supports fast inspection of multiple memories
- Avoids repeated List → Detail page → Back navigation
- Avoids shrinking/reflowing the list as a split panel would
- Can become near-full-screen naturally on smaller screens

### Required behavior

- Drawer overlays the current page rather than changing list width
- Background page remains visible
- Background may be slightly dimmed
- Drawer has its own vertical scroll container
- Current selected memory remains highlighted in the list
- Close methods:
  - Close button
  - Esc
  - Overlay click
- If unsaved edits exist, all close methods must trigger the unsaved-change flow

### Suggested desktop width

Start in the range of approximately `560–640px`.

Do not hard-code the design around an exact value; allow responsive width behavior.

---

## 3. View mode information hierarchy

Recommended order:

1. Header
2. Summary
3. Content
4. Categories
5. Keywords
6. Technical details / metadata

This order prioritizes human-readable memory content before storage/technical information.

### 3.1 Header

Show:

- Memory title if one exists; otherwise use a concise fallback derived from the memory
- Namespace
- Updated time
- `Edit` action
- `...` action menu
- Close button

Recommended action hierarchy:

```text
[ Edit ] [ ... ] [ × ]
```

`Delete memory` belongs inside the `...` menu.

Do not place Delete next to Save/Edit as a primary visual action.

---

## 4. Summary

Summary is a concise, scannable explanation of the memory.

Characteristics:

- Human-readable
- Shorter than Content
- Visually distinct from Content
- Normal prose rendering
- No heavy card treatment required

View mode should prioritize readability rather than displaying the raw stored representation.

---

## 5. Content and Markdown

Many memory bodies are Markdown.

### View mode

Render Markdown rather than showing Markdown source.

Support common constructs:

- Paragraphs
- H1/H2/H3 headings
- Bulleted and numbered lists
- Inline code
- Fenced code blocks
- Blockquotes
- Links
- Tables
- Horizontal rules

### Styling rules

Markdown must use the Remember Me design language rather than a generic README theme.

Recommended behavior:

- Headings compact enough for Drawer width
- Comfortable paragraph line height
- Code rendered using monospaced font
- Code block background visually distinct but subtle
- Tables scroll horizontally when necessary
- Long URLs wrap safely
- Images, if supported, must not exceed container width
- Avoid wrapping the whole content in a large gray card

### Safety

If Markdown is converted to HTML:

- Do not trust raw HTML from Markdown
- Sanitize generated HTML
- Block scripts / iframe / unsafe attributes
- Use safe link behavior

---

## 6. Categories

View mode:

- Display as compact chips
- Categories and Keywords remain visually separate because they have different semantics

Edit mode:

- Multi-select
- Existing values shown as removable chips
- `Add category` opens the same category-picker behavior used by the Memories filter where possible

---

## 7. Keywords

View mode:

- Display a useful subset as chips
- If there are many values, collapse using `+N`
- Allow expansion only if needed; do not flood the Drawer with dozens of chips

Edit mode:

- Search-to-add behavior
- Do not load/browse all 2000+ keywords as one static menu
- Use the same keyword picker/search component as the Memories filter where possible

---

## 8. Technical details

Recommended fields:

- ID
- Created At
- Updated At
- Namespace may also appear in header, but the technical value can remain available here if helpful
- Other read-only source/system fields if the current data model requires them

Recommended presentation:

- Visually quieter than Summary/Content
- Small label/value rows
- Optional collapsible section named `Technical details`

The technical section must not dominate the Drawer.

---

## 9. Edit mode

Entering Edit switches the same Drawer into editing state.

Do not navigate to another page.

### Editable fields

- Namespace
- Summary
- Content
- Categories
- Keywords

### Read-only fields

- ID
- Created At
- Updated At

### Layout principle

Keep View and Edit layouts spatially similar.

Example:

```text
View                Edit
Summary             Summary
text                [ textarea ]

Content             Content [ Edit | Preview ]
rendered markdown   [ markdown source ]
```

This reduces cognitive context switching.

---

## 10. Markdown Edit / Preview toggle

Use a two-state toggle:

```text
[ Edit | Preview ]
```

Do not use a side-by-side split preview in the Drawer because it leaves insufficient horizontal space.

### Edit state

- Shows raw Markdown
- Default state when entering Edit mode
- Supports multiline editing

### Preview state

- Renders the current unsaved Markdown
- Does not save
- Does not leave Edit mode
- Returning to Edit preserves the exact unsaved text

The toggle controls only the Content editing area; other fields remain editable in both states.

---

## 11. Primary actions in Edit mode

Replace View actions with:

```text
[ Cancel ] [ Save changes ] [ × ]
```

Recommended rules:

- `Save changes` is primary
- `Cancel` is secondary
- Save is disabled when the form is clean
- Save is enabled only when actual changes exist
- No autosave

---

## 12. Delete entry point

In View mode:

```text
... → Delete memory
```

Delete should use danger styling inside the menu.

Do not delete immediately.

Show confirmation before calling the API.

---

## 13. Delete confirmation

Suggested content:

```text
Delete this memory?

This action cannot be undone.

Hibernate optimistic locking...
ID: mem_8f3a2c1d

[ Cancel ] [ Delete ]
```

The confirmation should show enough information to identify the target.

Delete button is the danger action.

---

## 14. Visual state after list interaction

While Drawer is open:

- Keep the corresponding row selected/highlighted
- Preserve List scroll position
- Preserve pagination
- Preserve query
- Preserve namespace/category/keyword filters
- Preserve sort

The Drawer is an inspection/editing layer on top of the workspace, not a new workspace.
