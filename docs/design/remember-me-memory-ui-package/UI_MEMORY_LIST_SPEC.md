# Remember Me - Memory List Specification

## 1. Scope

This document defines the list portion of the Memories workspace only.

It intentionally does **not** define the Memory Detail / Edit / Delete experience.

---

## 2. List goals

The list should let a user answer these questions quickly:

1. What memories exist in the current scope?
2. What is each memory roughly about?
3. What category / keyword context does it have?
4. When was it last updated?
5. Which item should I open next?

The row should therefore optimize scanning rather than display every stored field.

---

## 3. Recommended row hierarchy

Each row should use this hierarchy:

```text
[icon]  Title                                             Updated time   ...
        One-line summary
        [tag] [tag] [tag] [+N]
```

### 3.1 Title

Primary visual anchor.

Rules:
- one line when possible
- medium/semibold weight
- truncate gracefully for extreme lengths
- do not use the raw memory body as the title if a summary/title field exists

### 3.2 Summary

Secondary line describing what the memory contains.

Rules:
- one line by default
- lower contrast than title
- ellipsis on overflow
- enough width to support fast scanning

### 3.3 Metadata chips

Use compact chips for categories / high-value tags.

Rules:
- show a small number (for example 3)
- collapse overflow to `+N`
- avoid dense walls of metadata
- chips are informational in the list; whether clicking a chip applies a filter can be decided later

### 3.4 Updated time

Place near the right edge.

Use human-readable relative time where appropriate:

```text
2h ago
1d ago
1w ago
```

Exact timestamp can be exposed later in Detail or tooltip if required.

### 3.5 Overflow menu

Reserve `...` at the row end for future row actions.

Current phase:
- the control may exist as a placeholder
- do not prematurely define Edit/Delete interaction here

---

## 4. Row interaction

Recommended behavior:

- entire row is clickable
- hover gives subtle background / border feedback
- selected/opening state can use a low-saturation blue highlight
- keyboard focus must be visible

Avoid making only the title clickable; the row is a single logical object.

---

## 5. Browse vs Search row

The base row structure should remain identical in both modes.

### Browse/List mode

```text
Title
Summary
Metadata chips
Updated time
```

No similarity score.

### Search mode

Same base structure, plus optional retrieval context if the API provides it:

```text
Title                              0.91  Semantic
Summary
Metadata chips                     2h ago
```

The retrieval score is supporting information, not the headline.

---

## 6. Density

Prefer a compact but breathable row.

A practical desktop target is approximately 72-96 px per row depending on typography.

Avoid large card tiles with excessive padding. The product should feel like a professional information tool, not a marketing dashboard.

---

## 7. Long content handling

### Long title
- single-line ellipsis
- full value available through future detail/tooltip

### Long summary
- single-line ellipsis in standard density
- do not let one row become taller than neighboring rows because of body length

### Many tags

```text
[tag1] [tag2] [tag3] [+7]
```

Do not wrap metadata chips onto multiple rows in standard list density.

---

## 8. Loading state

On initial load or query change:
- keep page shell, search, and filters stable
- replace rows with skeleton placeholders
- do not blank the whole page

If a previous result set is retained during refetch, visually indicate loading and avoid stale-selection confusion.

---

## 9. Empty states

Differentiate at least two cases.

### No memories exist

```text
No memories yet.
```

Potential future action can be added later if creation is supported.

### Filters/search return nothing

```text
No memories match the current search and filters.
[Clear filters]
```

Do not use the “empty database” message for a zero-result search.

---

## 10. Error state

Keep search/filter controls intact and replace result body with a contained error state:

```text
Could not load memories.
[Retry]
```

Do not destroy the user's typed query or selected filters on transient API failure.

---

## 11. Accessibility / interaction basics

- row focus state visible
- filter controls keyboard-operable
- checkbox multi-selects use accessible labels
- `...` menu has a descriptive accessible name
- do not rely on color alone to represent selected/active state

---

## 12. Visual reference

![List item design](images/03-list-item-design.png)

The information hierarchy is the important part. Detail/edit/delete content shown in any exploratory mockup is out of scope for this phase.
