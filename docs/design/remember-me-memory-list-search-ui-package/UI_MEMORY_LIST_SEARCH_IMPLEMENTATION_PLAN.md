# UI Memory List + Search Implementation Plan

## Phase 1 — State cleanup

Introduce separate:

```text
draftQuery
submittedQuery
```

Reason:
- typing should not trigger retrieval,
- submitted query determines Browse vs Search state.

## Phase 2 — Search mode model

Define:

```text
SearchMode = SEMANTIC | LEXICAL | ANCHOR
```

Default:

```text
SEMANTIC
```

Use a single-select segmented control.

## Phase 3 — One-line Search header

Implement one horizontal search row:

```text
SearchInput + SearchModeSegment + SearchButton
```

Responsive rule:
- Desktop/tablet: keep on one row.
- Only on genuinely narrow screens may the control collapse/wrap.

Do not deliberately create a second search-mode row on desktop.

## Phase 4 — Data-source router

Centralize API selection:

```text
if submittedQuery is blank:
    listMemories(filters, paging, sort)
else:
    switch searchMode:
        SEMANTIC -> semanticSearch(...)
        LEXICAL  -> lexicalSearch(...)
        ANCHOR   -> anchorSearch(...)
```

Presentational components should not choose APIs themselves.

## Phase 5 — Shared filters

Implement:
- Namespace single-select
- Categories searchable multi-select
- Keywords remote searchable multi-select

Keywords:
- do not preload 2000+ values,
- debounce suggestion calls,
- pass current scope where supported.

## Phase 6 — Unified row

Use one row/card component for Browse and Search.

Add optional retrieval context.

Do not add retrieval icons.

Do not add an aggregate percentage.

## Phase 7 — Raw-value mapping

Map backend response literally.

Examples:

```text
semantic score 0.86 → "Semantic 0.86"
FTS score 0.62      → "FTS 0.62"
anchor hit          → "Anchor"
```

Do not:
- multiply by 100,
- convert to `%`,
- normalize,
- combine with another score.

## Phase 8 — Mode switching

With active query:
- switch mode,
- reset page,
- call new Search API immediately.

Without active query:
- switch selected mode only.

## Phase 9 — Result ordering

Trust selected backend search method ordering.

No cross-mode merge or fusion.

## Phase 10 — Tests

Minimum tests:

1. Page open calls List API.
2. Typing does not call Search API.
3. Enter submits Semantic search by default.
4. Search button behaves same as Enter.
5. Semantic → Lexical with active query immediately calls Lexical API.
6. Lexical → Anchor with active query immediately calls Anchor API.
7. Mode switching with no submitted query makes no search call.
8. Clearing submitted query returns to List API.
9. Browse row shows no retrieval indicator.
10. Semantic row shows only raw Semantic value.
11. Lexical row shows only raw FTS/Lexical value.
12. Anchor row shows only Anchor evidence.
13. No row contains an aggregate percentage score.
14. Filters route to the currently active List/Search API.
15. Keyword picker does not preload all 2000+ keywords.
16. Search mode control remains inline with search input on desktop.
