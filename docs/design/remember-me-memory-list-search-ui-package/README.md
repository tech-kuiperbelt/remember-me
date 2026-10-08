# Remember Me — Memory List + Search UI Design Package

This package captures the finalized design for the Memories browsing/search surface.

## Scope

Included:
- Unified List + Search experience
- One-line search header
- Semantic / Lexical / Anchor single-select mode
- Namespace / Categories / Keywords filters
- Browse/List API vs Search API switching
- Search-result retrieval indicators using raw backend values
- Result list layout and interaction rules
- Implementation guidance

Excluded:
- Detail Drawer
- Edit / Preview / Delete
- Hybrid search
- Multi-source merge/fusion
- Any synthetic/overall relevance score

## Final decisions

1. Query empty => List API.
2. Submitted non-empty query => Search API for the selected mode.
3. Search mode is single-select: Semantic / Lexical / Anchor.
4. Default mode is Semantic.
5. Mode selector stays on the same row as the search input and Search button.
6. Switching mode with an active query immediately re-runs the same query.
7. Filters are shared by List and Search.
8. Search results show only the active mode's raw backend indicator/value.
9. No generic percentage score such as `92%`.
10. No retrieval-mode icons; use plain text labels to reduce learning cost.
