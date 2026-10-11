# Retrieval Statistics UI — Final Design

Date: 2026-10-10. Theme and Shell rules: see `STATS_SHARED_SHELL_AND_NAVIGATION.md` (authoritative).

## Hierarchy

1. Shared Statistics title + Namespace selector.
2. Shared Memory/Retrieval wide segmented switch (Retrieval selected), and 7/30/90 Days on right.
3. **One consolidated Overview panel**, three values separated subtly: `Searches`, `Rated Searches`, `Feedback Coverage`. NOT three independent KPI cards. No arrows, growth percentages, or fake deltas.
4. Full-width **Search Activity** chart: single line showing search calls over time; no multi-mode overlays.
5. Two equal-information cards on wide screens: **Search Modes** and **Feedback**; stack in one column on narrow screens.

No redundant page introduction card. No storage-health/embedding diagnostics, recall accuracy, Hit@1 or Hit@5, or token counts. Retain existing Shell exactly.

## Overview definitions

- `Searches`: total searches, includes successful and failed calls.
- `Rated Searches`: unique successful searches with >=1 qualifying valid feedback, deduplicated via backend.
- `Feedback Coverage`: rated_searches / successful nonempty searches, NOT satisfaction and NOT search recall. When eligible searches = 0, API percentage is null; show em dash `—`, not `0%`.
- If needed explanatory copy is small and restrained; all metrics use the same namespace and calendar-day window. Do not mix entire-audit values with window-specific values.

## Search Activity

- Single blue line chart, no zoom/drag/select/click-through.
- 7 Days = 7 daily buckets; 30 Days = 30 daily buckets; 90 Days = weekly buckets with explicit dates from API. Retain all buckets, including zeros; don't alter aggregation just because the viewport is narrow.
- Hover / keyboard focus (and touch where possible) tooltip: local day or start/end of week + exact search count. Chart labels should be readable in Light and Dark, fewer visible x ticks on narrow screens. Use API bucket bounds, not fabricated dates.
- If no searches, show a calm empty message in plot area; don't draw a misleading line or random fake values.

## Search Modes

- Semantic / Lexical / Anchor / Meta always present, sorted count descending (stable tie ordering).
- Every row: mode name, number of calls, percent of total calls, thin horizontal proportional bar; 0-count modes remain visible.
- `total_searches=0`: counts = 0, percentage UI as 0% with sensible label; avoid division-by-zero. All calls include failed ones.
- No donut here; match existing Memory metadata ranked-bar visual language.

## Feedback

- Show `Average Rating` on a **-10 to +10 numeric scale**, never a five-star score. The later user-approved ten-star decoration is described below. For empty valid ratings show `—`.
- Show the total valid deduplicated ratings in the footer explanation, following `Based on reported ratings only, not all search results.` as `Valid ratings: N.`; do not present it as a separate summary metric.
- Three categories: Positive (>0) green; Neutral (=0) blue; Negative (<0) muted red. Display count and percent based on **valid rating_count**, plus thin bars.
- Never repeat Feedback Coverage in this panel (it is in Overview). Add a short understated qualification such as `Based on reported ratings only, not all search results.`
- If searches exist but zero valid ratings: show `No feedback available for this period`; don't show 0% sentiment bars implying negative feedback.
- Test reports were excluded by backend when declared; never claim all unclassified feedback is non-test.

## Time and namespace

- Common Namespace selector scopes search events, and reports inherit scope via associated search_id (handled in API).
- 7/30/90 Days use calendar dates in browser's IANA timezone. Date range shown in a secondary caption/tooltip if helpful; acknowledge retained Audit history can be incomplete.
- `available_from` is not sufficient by itself to assert missing history. If backend completeness unknown, do not label coverage as verified complete.
- Distinguish no search data from API/file-read failure and from no feedback data.

## Responsive and layout

- >=1200px content: three Overview fields side-by-side within ONE panel; full-width chart; Search Modes and Feedback two columns.
- 700–1199px: keep Overview three fields if readable; lower two columns only while readable, otherwise auto-stack.
- <700px: Overview wraps; lower panels stack; chart x-axis labels thin out (not data points); view and days controls stack vertically on their own lines.
- Fixed consistent switch geometry across Memory/Retrieval and Light/Dark at same viewport size; no accidental height/width shift as the time picker disappears.

## Accessibility / visuals

- Use existing app design tokens, compact spacing, restrained subtle borders. Semantic colors only where meaningful.
- Search trend tooltips via hover/focus; native semantics for segmented controls, labels, focus state, text contrast and charts with supporting text or tabular summary if needed.
- Keep loading skeleton/error states stable so layout does not jump significantly.

## Sample-image exclusions (CRITICAL)

The four sample PNGs are **composition references only**. In particular, do NOT implement their invented plus-percent growth badges, sometimes independent Overview cards, the five-star feedback scale, references to `2024`, or made-up data. For Memory Top-N, use 5/30/100 regardless of any image labels. Current accepted Shell wins over mockups.

## Confirmed diagnostics behavior

The Data notes disclosure is available only when Debug is enabled in configuration. It describes global scan diagnostics and never attributes them to the selected namespace/period. File-read/API errors remain visible in every mode. Do not claim complete history from available_from. Show the last weekly bucket as partial through window_end_utc, even when end_utc denotes the next calendar midnight.

Confirmed Overview icon treatment: Searches uses a blue search icon, Rated Searches a purple feedback/check icon, and Feedback Coverage a green target icon. Reuse Memory Overview badge sizing and responsive placement; icons are decorative and do not add metrics.

Confirmed lower-panel palette: Semantic blue, Lexical purple, Anchor green, Meta gold, fixed by mode rather than ranking. This follows the user's latest request for multiple colors even though sample Search Modes bars are all blue. Feedback follows the sample's green / blue / muted red for positive / neutral / negative, while retaining the real −10..10 scale. Search Activity stays a single blue series.

Final typography/density audit shares Memory's headings, Overview labels/values and badge sizing. Mode and sentiment bars use an 8px fill and inline layout from 360px panel inner width; narrower panels keep a separate bar row. Trend labels are 12px and the single line is 2.25px. Static cards remain flat; only floating tooltip uses the established shadow token.

Footer explanation starts with a decorative 16px information-circle icon, with an 8px gap and alignment to the first text line. Keep Valid ratings in the same explanatory text.

User-approved Average Rating presentation: keep the actual −10..+10 numeric value and scale caption, and place ten decorative stars beside the 28/36px number. Stars are 18px with a 3px internal gap, gold filled for positive values and semantic red for negative values, with muted empty stars. Fill is min(abs(rating), 10): positive values fill left to right, negative values right to left, including fractional clipping (7.57 → 7 full and 57% of the eighth; −7.57 fills the corresponding stars from the right). Zero shows ten empty stars; null shows — with the star group hidden. Stars are not an input or a conversion to another score scale; hide them from assistive technology. On narrow panels wrap the entire star group together, preserving 12px caption text.

The visible tab label is **LLM Retrieval**. The date/time context includes **MCP search activity and reported feedback**, using the existing 12px secondary-text style. This label change does not alter event inclusion rules.
