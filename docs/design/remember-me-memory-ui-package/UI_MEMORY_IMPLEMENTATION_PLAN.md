# Remember Me - Memories UI Implementation Plan

## 1. Implementation principle

Implement in small vertical slices and keep the existing API/domain behavior intact.

Do not introduce a new “Search page” route. Search is a state of the Memories workspace.

Do not implement Memory Detail/Edit/Delete in this phase beyond an event/navigation hook.

---

## 2. Suggested component structure

Names are illustrative; adapt to the current frontend stack.

```text
MemoriesPage
├── MemoriesHeader
│   ├── MemorySearchBar
│   └── MemoryFilters
│       ├── NamespaceSelect
│       ├── CategoryMultiSelect
│       └── KeywordSearchMultiSelect
├── MemoriesResultHeader
│   ├── ResultCount
│   └── SortSelect
├── MemoryList
│   ├── MemoryListItem
│   ├── MemoryListSkeleton
│   ├── MemoryListEmpty
│   └── MemoryListError
└── Pagination
```

Keep data fetching/state orchestration in the page/container layer. Keep visual components as stateless as practical.

---

## 3. Query state model

Recommended state:

```text
inputQuery
submittedQuery
namespace
categories[]
keywords[]
sort
page
pageSize
```

Derived mode:

```text
mode = submittedQuery.trim().length > 0 ? SEARCH : LIST
```

Do not derive API mode from `inputQuery`, otherwise typing changes the data source before Search is submitted.

---

## 4. Request selection

Pseudo-code:

```text
if mode == LIST:
    listMemories({
      namespace,
      categories,
      keywords,
      sort,
      page,
      pageSize
    })
else:
    searchMemories({
      query: submittedQuery,
      namespace,
      categories,
      keywords,
      sort,
      page,
      pageSize
    })
```

Both calls should map into a common front-end result shape where possible:

```text
MemoryResultPage {
  items[]
  total
  page
  pageSize
}
```

Search-specific fields such as relevance score may be optional properties on each result.

---

## 5. Phase 1 - Static shell and layout

Implement:
- Memories route/page
- title
- search bar
- filter row placeholders
- result header
- static list rows
- pagination shell

Goal:
- confirm spacing, information hierarchy, responsive wrapping, light/dark theme compatibility

Do not connect APIs yet if that slows visual iteration.

Reference:
- `images/01-memories-search-list-overview.png`

---

## 6. Phase 2 - List API

Connect List API first.

Implement:
- initial load
- total count
- page/pageSize
- sort
- loading skeleton
- empty state
- retryable error state

Acceptance:
- opening Memories with no query always uses List API
- list rendering does not depend on Search API

---

## 7. Phase 3 - Common filters

### Namespace
- single select
- apply immediately
- reset page to 1

### Categories
- multi-select
- searchable local/remote list depending on existing API
- approximately 25 candidates are browseable
- apply selection to the active mode

### Keywords
- searchable multi-select
- remote suggestions
- do not fetch/render all 2000+ as a dropdown
- selected values appear as chips/compact count

Every filter change:
- reset page to 1
- re-fetch using whichever mode is currently active

---

## 8. Phase 4 - Search API switching

Implement search submission:

```text
Enter / Search button
-> submittedQuery = inputQuery.trim()
-> page = 1
-> Search API
```

Clearing the input:

```text
inputQuery = ""
submittedQuery = ""
page = 1
-> List API
```

Preserve filters when switching List <-> Search.

Do not auto-submit every keystroke.

---

## 9. Phase 5 - Search-result context

If supported by Search API, add optional secondary fields:
- score
- retrieval source/mode

Rules:
- not visible in List mode
- visually secondary
- do not change the fundamental row layout

---

## 10. Phase 6 - Interaction polish

Implement:
- row hover
- keyboard focus
- filter popover keyboard handling
- selected chips removal
- `+N` overflow presentation
- reset filters
- stable loading transition
- URL/state persistence only if desired

Potential URL representation:

```text
/memories?q=hibernate&namespace=remember-me&categories=backend,architecture&page=1
```

This is optional for the first pass, but useful if shareable state/history navigation matters.

---

## 11. Detail boundary for this phase

Expose only a callback/router action:

```text
onMemoryOpen(memoryId)
```

Possible temporary behavior:
- no-op placeholder during early phases
- navigate to an existing detail route if one already exists
- open a blank/detail shell if necessary

Do **not** implement new edit/delete behavior as part of Search + List work.

---

## 12. Suggested acceptance checklist

### Search/List switching
- [ ] Empty submitted query -> List API
- [ ] Submitted non-empty query -> Search API
- [ ] Clearing query -> List API
- [ ] Typing without submitting does not call Search API

### Filters
- [ ] Namespace works in List and Search modes
- [ ] Categories multi-select works in both modes
- [ ] Keywords multi-select works in both modes
- [ ] Keyword picker does not load 2000+ options at once
- [ ] Filter changes reset pagination
- [ ] Reset filters does not unexpectedly clear search query

### List
- [ ] title + summary + compact metadata + updated time
- [ ] long text truncates cleanly
- [ ] metadata overflow collapses to `+N`
- [ ] loading / empty / error states implemented
- [ ] pagination and page size work
- [ ] Browse mode does not show relevance score
- [ ] Search mode may show score if available

### Scope control
- [ ] No independent Search route/page introduced
- [ ] Detail/Edit/Delete are not expanded in this phase

---

## 13. Recommended Codex implementation instruction

A concise instruction for the coding agent:

```text
Implement the Memories workspace according to UI_MEMORY_SPEC.md and
UI_MEMORY_LIST_SPEC.md.

Scope is strictly Search + common Filters + List + pagination/states.
Do not design or implement Memory Detail/Edit/Delete beyond a click hook.
Do not create separate List and Search pages. The data source switches based
on submitted search query: blank -> List API, non-blank -> Search API.
Preserve existing APIs and business behavior unless an explicit API change is
required; if a missing keyword-suggestion API blocks the design, isolate that
need and report it rather than inventing unrelated backend behavior.
```
