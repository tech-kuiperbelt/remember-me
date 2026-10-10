# Remember Me MCP — Stats UI Design

Status: Approved design decisions, ready for Codex plan review
Date: 2026-10-10
Scope: Stats UI only. No redesign of approved Shell, Memory List, Search/Filters or Memory Detail.

## 1. Principles
- Professional, technical, compact, cohesive, usable; no decorative charts or unnecessary icons.
- Light and Dark themes have **identical layout, information, chart types, interactions, spacing and typography**. Only color-related tokens change.
- Existing Remember Me Shell, sidebar/header, navigation and responsive behaviors are authoritative. Concept mockups are not literal specifications where they differ.
- UI is based on actual Stats metadata API data; no invented metrics such as tokens, growth rate or recall hit rate.
- Multilingual support is a cross-application follow-up: use translatable labels, avoid fixed-width text assumptions, do not implement i18n as part of this work.

## 2. Content hierarchy
1. Page header: `Statistics` + Namespace select (default All Namespaces).
2. A **single Overview panel** containing five metrics: Memories, Namespaces, Categories, Keywords, Anchors. Use subtle separators rather than five independent KPI cards; wrap gracefully on narrow screens.
3. Namespace Distribution panel, shown **only for All Namespaces**: a donut chart + full namespace list. Each list row shows name, memory count, and percent. Click row to open Memory List browsing that namespace. Do not truncate the actual list or paginate it.
4. Metadata panels for Categories, Keywords, Anchors: shared component and visual language, each with title, independent Top 5/30/100 control, Unique Count, Coverage value/progress bar, and ranked items.

## 3. Metrics and semantics
- Scope: non-deleted memories only, all namespaces when no namespace filter; one selected namespace otherwise.
- Overview Memories = memory count in selected scope. Namespaces = distinct namespaces with live memories in selected scope; other overview values are actually-used distinct category/keyword/anchor values in selected scope. Category seed values with no associated live memory do not contribute.
- Coverage: number of unique memories carrying >=1 effective value of the dimension divided by the selected scope's total memories (0% when denominator 0).
- Ranking item: value, number of distinct memories in scope with that value, and percent of memories in scope (`count / total_memories`). A memory with multiple terms can count toward multiple rows; percentages need not sum to 100%.
- Order by descending memory count, then ascending value for ties. Preserve existing anchor case-sensitive cross-memory counting semantics.
- Donut distribution denominators: all-scope live memories; list shows exact count and computed share. For many small slices, visual consolidation to `Others` is permitted **in donut only**; full clickable list stays complete.
- Do not display operational/diagnostic backend, replica, embedding profile or NDJSON fields, nor unavailable recall metrics.

## 4. Interactive behavior
- Changing Namespace selector refreshes all in-scope values and metadata panels immediately, without Apply. Always keep the selector's global options available.
- In All view: show donut and complete namespace list. In selected namespace view: hide entire distribution panel (not a meaningless 100% ring).
- Clicking a namespace list row opens Memory List in Browse mode, clearing old query/filters/dates/page. The header selector filters Stats itself. Returning restores the Stats context.
- Categories, Keywords, Anchors have **independent** Top 5/30/100 selectors; default each to 5. Use first N of the API's up-to-100 ranking entries, no additional API request on Top N change.
- Top 5 lists grow naturally. Top 30/100 list areas have their own max-height scrolling (target 400–480px), while title, counters, coverage and selector remain visible. Changing Top N scrolls the relevant list to its top.
- Ranked item drilldown goes to existing Memory List search/filter behaviors: Category = exact category filter; Keyword = exact keyword match; Anchor = anchor search mode. Preserve currently selected Stats namespace in the transfer. Reuse existing, verified search semantics; never silently substitute fuzzy search for exact keyword.
- Returning from Memory List to Stats restores namespace, each Top N selection, internal ranking scroll positions and page scroll position. Data may refresh; view preferences persist. Prefer view-scoped state in the current Web UI session; do not require backend persistence.
- Ensure keyboard accessibility, visible focus, readable hover/click target, meaningful progress/chart labels, and empty/loading/error states without fake data.

## 5. Responsive layout
Use the Stats content container width, including sidebar expansion effects; CSS Container Queries preferred when supported:
- >=1200px: Metadata panels in 3 columns.
- 700–1199px: 2 columns; third panel wraps to next row.
- <700px: 1 column, **fully visible panels**, no default accordions.
- Donut + full Namespace list side by side where space permits; stacked vertically on narrow screens.
- Overview five values use wrapping/reflow without clipping long translated labels.
- Regardless of breakpoint or theme, Top 30/100 scroll belongs to individual ranked lists and not the whole panel.
- Breakpoint numbers are initial design targets, to tune against real layout/content without changing behavioral requirements.

## 6. Visual style
- Reuse established Shell tokens, typography, spacing, borders, radii and Light/Dark color system.
- One Overview container, low-contrast internal separators, readable numeric hierarchy and restrained accent color.
- A donut is the sole Namespace proportion visualization; **do not also add a stacked percentage bar**.
- Metadata ranks: label, count + percent, and slim bar whose length represents percentage of all selected-scope memories (not relative to highest ranked item).
- Use theme-appropriate color palette for donut slices; preserve semantic mapping of namespace colors across light/dark as feasible.
- Avoid unnecessary icons, extra sidebar sections, user avatar, search header, growth stats or other elements that appeared in generated concept art.

## 7. Acceptance checklist
- [ ] Same geometry, widgets and behavior in Light/Dark; color treatment only varies.
- [ ] No regression to approved Shell/List/Search/Detail.
- [ ] All five overview metrics and three metadata panels rendered from real API data.
- [ ] Namespace selector works; full clickable distribution appears only in All view.
- [ ] Correct unique-count/coverage/percent semantics and zero/empty handling.
- [ ] Top 5/30/100 independent and internally scrollable for long rankings.
- [ ] Correct drilldown and Stats view restoration.
- [ ] All three responsive breakpoints and keyboard navigation validated.

## Confirmed mockup refinements (2026-10-10)

- Add distinct document / layers / tag / hash / anchor Overview icons, using blue / purple / green / gold / pink with subtle badge surfaces. Narrow screens place icons above labels.
- Use a visibly thicker donut (44 SVG units at radius 80) and percentage labels for slices at least 7%; retain all exact percentages in the complete list. Add Namespace / Count / Percentage column headers aligned with values.
- Move Top 5 / 30 / 100 to the Unique Count / Coverage summary row and separate those metrics with a vertical rule. At less than 360px panel inner width, wrap the entire Top group together.
- Add stable Debug IDs for unique count, coverage percentage, count ratio and progress, as recorded in 53.4.

Further confirmed refinements: the thick donut uses a symmetric padded viewBox (`-6 -6 212 212`) to avoid clipping; Unique Count uses content width with a 12px gap before the Coverage rule; Keyword ranking bars cycle the eight-color palette in both themes. Color distinguishes items, while bar length continues to represent memory share.

Follow-up confirmation: apply the same cycling ranking palette to Categories and Anchors as Keywords. All three dimensions share colors by rank position; no semantic meaning is assigned to these colors.

Final typography/density audit: panel headings 16/24px; Overview labels 13/20px, values 28/36px; desktop icon badges 48px with 24px SVG. Ranking bars are 8px and share the label/count/percentage row when panel inner width is at least 360px; narrower panels keep the bar below text. Static cards remain flat with 1px subtle borders. Existing Coverage, scopes and drilldown semantics are retained.
