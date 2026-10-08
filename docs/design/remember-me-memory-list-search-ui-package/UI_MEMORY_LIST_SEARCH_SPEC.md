# UI Memory List + Search Specification

## 1. Page model

Memories uses one workspace for browsing and searching.

```text
Search row
Filter row
Result header
Result list
```

There is no separate List page and Search page.

## 2. Search row

Keep the search controls on one line:

```text
[ Search memories... ] [ Semantic | Lexical | Anchor ] [ Search ]
```

Rules:
- Search input, search mode selector, and Search button remain on the same row.
- Do not place the mode selector on a separate line.
- Search mode uses a compact segmented control.
- Default mode is `Semantic`.

## 3. Search modes

Exactly one mode can be active:

```text
Semantic
Lexical
Anchor
```

No checkbox multi-select.

No Hybrid mode in this scope.

### Semantic
Meaning-based / embedding retrieval.

Search result indicator example:

```text
[Semantic 0.86]
```

### Lexical
Full-text / keyword retrieval.

If the backend returns an FTS score, display the backend terminology/value:

```text
[FTS 0.62]
```

### Anchor
Anchor-based retrieval.

If there is only hit/no-hit information:

```text
[Anchor]
```

If the backend exposes a raw anchor-specific value, it may be shown without conversion.

## 4. API switching

### Browse mode

When submitted query is empty:

```text
query == empty
→ List API
```

### Search mode

When submitted query is non-empty:

```text
query != empty
→ Search API selected by mode
```

Example:

```text
Semantic → semanticSearch(...)
Lexical  → lexicalSearch(...)
Anchor   → anchorSearch(...)
```

Clearing the submitted query returns to List API.

## 5. Query submission

Do not search on every keystroke.

Recommended behavior:
- User types query.
- Enter or Search button submits it.
- `draftQuery` becomes `submittedQuery`.
- Current selected mode executes.

Clearing the query returns to browse mode.

## 6. Search-mode switching

When an active submitted query exists:
- Switching Semantic / Lexical / Anchor immediately re-runs the same query.
- Keep current filters.
- No second click on Search is required.

When no submitted query exists:
- Update selected mode only.
- Do not call a Search API.

## 7. Shared filters

List and Search share:

```text
Namespace
Categories
Keywords
```

Recommended layout:

```text
[ Namespace: All ▾ ] [ Categories: All ▾ ] [ Keywords: All ▾ ]
```

### Namespace
- Single-select.

### Categories
- Multi-select.
- Approx. 25 values.
- Browseable and searchable.
- Checkbox picker is appropriate.

### Keywords
- Multi-select.
- 2000+ values.
- Do not load or browse the entire collection.
- Use remote search suggestions.
- Search-to-add behavior.
- Summarize selection as chips and/or `+N`.

Keyword suggestions should be scoped by namespace/categories where supported.

## 8. Filter semantics

Preferred semantics:

```text
same dimension → OR
different dimensions → AND
```

Example:

```text
namespace = remember-me
categories = [backend, architecture]
keywords = [hibernate, oracle]
```

means:

```text
namespace = remember-me
AND
(category = backend OR category = architecture)
AND
(keyword = hibernate OR keyword = oracle)
```

## 9. Result header

Show compact status information:

Browse:

```text
324 memories
```

Search:

```text
Found 8 results (132 ms)
```

Sorting may appear on the right.

Do not introduce an artificial overall score.

## 10. List result structure

Recommended row hierarchy:

```text
Title
Summary
Retrieval indicator (search only) + category/keyword chips + updated time
```

Example browse row:

```text
Hibernate optimistic lock exception
Discussed stale state and @Version behavior...
[Java] [Hibernate] [architecture]      Updated Sep 20, 2026
```

Example semantic-search row:

```text
Hibernate optimistic lock exception
Discussed stale state and @Version behavior...
[Semantic 0.86] [Java] [Hibernate] [architecture]   Updated Sep 20, 2026
```

Example lexical-search row:

```text
Hibernate optimistic lock exception
Discussed stale state and @Version behavior...
[FTS 0.62] [Java] [Hibernate] [architecture]        Updated Sep 20, 2026
```

Example anchor-search row:

```text
Hibernate optimistic lock exception
Discussed stale state and @Version behavior...
[Anchor] [Java] [Hibernate] [architecture]           Updated Sep 20, 2026
```

## 11. Retrieval indicators

Important rules:
- No icon vocabulary for Semantic / Lexical / Anchor.
- Use text labels.
- Show only the active mode's indicator.
- Show backend raw values.
- Do not convert to percentages.
- Do not normalize.
- Do not add a second overall relevance score.
- Do not show inactive-mode placeholders.

Avoid:

```text
92%
Semantic 0.86
```

Use only:

```text
Semantic 0.86
```

## 12. Result ordering

Because search mode is single-select, there is no result merge/fusion problem.

Use backend ordering from the selected search method.

The frontend must not:
- fuse multiple result sets,
- re-rank between retrieval modes,
- compute RRF,
- compute weighted combined scores,
- normalize source-specific scores.

## 13. Browse vs Search consistency

The same list component should render both states.

Conceptually:

```text
MemoryRow
  memory
  retrievalContext?   // absent in browse mode
```

Browse mode:
```text
retrievalContext = null
```

Search mode:
```text
retrievalContext = {
  mode: "semantic",
  rawValue: 0.86
}
```

## 14. Visual principles

- Compact, professional, tool-like.
- Search mode belongs to the search action, so it stays inline with the search box.
- Filters are secondary scope controls.
- Result evidence must be explicit and literal.
- Avoid decorative concepts that users must learn.
- No ambiguous aggregate percentages.
