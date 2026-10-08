# UI Memory List + Search Interaction Rules

## State

Recommended state:

```text
draftQuery
submittedQuery
searchMode
namespace
categories[]
keywords[]
sort
page
pageSize
```

## Page open

```text
submittedQuery = empty
→ List API
```

## Typing

```text
update draftQuery
→ no Search API call
```

## Submit search

Via Enter or Search button:

```text
submittedQuery = draftQuery.trim()

if submittedQuery != empty:
    call selected Search API
else:
    call List API
```

## Clear query

```text
draftQuery = ""
submittedQuery = ""
→ List API
```

## Change search mode

If `submittedQuery` is non-empty:

```text
change searchMode
→ immediately re-run current query
```

If `submittedQuery` is empty:

```text
change searchMode only
→ no Search API call
```

## Change filters

Browse state:

```text
→ call List API with current filters
```

Search state:

```text
→ re-run active Search API with same submitted query
```

## Pagination

Changing search mode or filters should normally reset page to the first page.

Changing page/page-size calls the currently active data source.

## Loading

Keep:
- query,
- selected mode,
- filters

stable while the result area loads.

## No results

```text
No memories found
Try changing the query or filters.
```

## Error

Preserve query/mode/filters and expose Retry.

## Result click

Clicking a row may open the Detail Drawer, but Detail behavior is outside this package.
