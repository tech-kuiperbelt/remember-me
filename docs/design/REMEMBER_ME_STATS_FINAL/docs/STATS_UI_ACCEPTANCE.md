# Statistics UI — Final Acceptance Checklist

## Core composition
- [ ] Existing Remember Me Shell unchanged: header, sidebar icon/labels, collapsed state, persistence and navigation.
- [ ] Statistics page header and common Namespace selector retained.
- [ ] Memory/Retrieval wide segmented switch has identical **position/width/height** for BOTH active tabs, in BOTH themes, at matching viewport width.
- [ ] Memory shows blank right-side space; Retrieval shows 7/30/90 Days at right; at narrow widths controls stack consistently.
- [ ] No separate Retrieval introduction card and no new sidebar item.

## Memory (accepted content)
- [ ] One Overview panel with five counts: Memories, Namespaces, Categories, Keywords, Anchors.
- [ ] All-scope Namespace donut + complete clickable list; hide distribution for selected namespace.
- [ ] Three dimension panels: unique, coverage, independent Top 5/30/100; lists scroll internally only for 30/100.
- [ ] Correct count/percentage denominators, full scoped filtering, drilldown, no zero-count Category seeds.
- [ ] No unsupported growth metrics, Memory Activity chart or alternate 5/10/20 limits from mockups.

## Retrieval (new content)
- [ ] One three-value Overview panel: Searches, Rated Searches, Feedback Coverage.
- [ ] 7/30 daily and 90 weekly single-line Search Activity, tooltip with correct local time/date ranges.
- [ ] Four modes shown including zeros, ranked by count; calls include failed searches.
- [ ] Feedback numeric average -10..10; positive/neutral/negative counts & percentages; no star rating or conflation with recall accuracy.
- [ ] Coverage denominator is successful, nonempty searches; null/zero cases display honestly.
- [ ] Time-zone, selected namespace, selected days passed to API. Accurate error/loading/no-search/no-feedback states.

## State, theme and responsive
- [ ] In-app navigation restores view, namespace and preferences with fresh data; F5 resets Stats preferences and uses the normal Memories entry.
- [ ] Fast filters cannot be overwritten by stale responses; scoped statistics never contaminate global namespace/category registries.
- [ ] Light/Dark exact same structure, spacing, chart types and behavior. Only color tokens differ.
- [ ] ≥1200px / 700–1199px / <700px checked with sidebar both expanded and collapsed; keyboard/touch accessible.
- [ ] Cross-check against all images as visual-only references, not as metric specifications.

## Implementation governance
- [ ] Codex Coding Plan reviewed before edits.
- [ ] No unrelated application or backend changes.
- [ ] Actual Retrieval API shape verified at implementation time.
- [ ] Automated and manual test results recorded; specific remaining known limitations disclosed.

## Confirmed follow-up decisions

- Remove the local Stats Refresh button; use the existing toolbar Refresh for the active subview, retaining preferences.
- Shared Namespace options come from the existing global registry plus configured default, independent of loading Memory. Scoped responses never mutate global candidates.
- Data notes appear only when Debug is enabled in configuration (save then refresh the page). Diagnostic counts describe the global retained-audit scan. Ordinary mode still displays API errors and Retry.
