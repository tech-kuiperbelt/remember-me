# Remember Me MCP — Stats UI Implementation Guide

Status: Plan input; **do not implement until Coding Plan is reviewed and approved**
Date: 2026-10-10

## 1. Boundary and prerequisites
This package targets the **Stats frontend only**. Backend Stats metadata support is reported complete; inspect the **actual current API contract** before coding. Do not assume example fields match production field names. Do not edit backend APIs, Storage SQL, MCP contracts, or already approved Shell / Memory List / Search / Detail design without explicit approval.

Known prior paths (verify in repository):
- `web/index.html`: Stats page container.
- `web/static/js/stats.js`: existing Stats load/render code; historically also initialized global Namespace/Category options and default Namespace.
- `web/static/js/main.js`: navigation/initialization and global reload.
- `web/static/js/keywords.js`: existing keyword browse/filter flow.
- `web/static/css/*` (inspect actual files): Shell and Stats styles.
- `GET /api/stats`: historic global stats and operational checks.
- Suggested/previously designed `GET /api/stats/metadata?namespace=...`: confirm it is implemented and inspect its actual JSON and error conventions.

**Critical dependency:** existing Stats loading participates in global Namespace and Category registry initialization used by other pages. Scoped Stats response must NEVER overwrite global option registries, and removal of the old stats loader requires an explicitly reviewed replacement. Preserve existing side effects until the code dependency is fully understood.

## 2. Planning sequence for Codex (Plan Mode)
1. Verify backend endpoint's actual response, error handling, null/all namespace representation and full candidate namespace list; compare to `STATS_UI_DESIGN.md`.
2. Inspect current DOM, view lifecycle and shared global registration state; identify minimal safe touch points and exact affected files.
3. Define UI data-to-view mapping and empty/loading/error behavior, cancellation or request-sequence guards to avoid stale responses on rapid scope changes.
4. Design reusable dimension panel component/rendering function for Categories, Keywords, Anchors; map count, coverage, Top lists and drilldown distinctly.
5. Specify navigation to existing Memory List **using actual existing filter/search APIs**; confirm exact Category, Keyword and Anchor behavior. Escalate incompatibilities rather than approximate silently.
6. Plan state persistence across in-app page navigation, including namespace selection, each Top N, page scroll and list scroll. Limit to existing session/view state approach; don't add cross-Tab communication.
7. Plan CSS/layout implementation using existing tokens and container-width breakpoints; unchanged geometry across Light/Dark.
8. Outline tests and manual acceptance matrix; submit plan for review. No application code edits in Plan Mode.

## 3. Execution increments (after approval)
### Stage A — Adapter and safeguards
- Implement an API adapter for scoped metadata stats using the actual backend contract.
- Keep global registries unmodified by scoped results. Add stale-request guards and safe loading/empty/error handling.
- Verify All vs concrete Namespace, including empty valid default namespace and unknown namespace errors.

### Stage B — Stats structure
- Render page header + independent namespace filter.
- Render one Overview panel with exactly five agreed metrics.
- Render conditional donut + full namespace list for All, plus namespace click-through to Memory List Browse.
- Render three reusable metadata panels with Unique, Coverage and percent-based ranking bars.

### Stage C — Interactions
- Each metadata panel has independent Top 5/30/100 state and scrollable rankings for long views.
- Implement precise click-to-Memory-List drilldown through existing filter/navigation mechanisms; preserve current selected namespace.
- Implement restoration of Stats UI context on return, without overwriting other pages' view state.

### Stage D — Responsive themes and polish
- Apply 3/2/1 column metadata breakpoints based on content width.
- Stack donut and list on narrow screens; retain full panels, no accordion.
- Ensure light/dark theme parity in structure and semantic colors, keyboard accessibility, long labels and translations readiness.

## 4. Verification cases
- Data: all/single/empty namespace, 0 memories, absent dimension values, seed-only categories, duplicate terms in memory, deleted records, anchor case variants, sparse (<5) and very large (>100) rankings.
- Accuracy: percentages use scoped total; Coverage counts *distinct memories*, not sum of term frequencies; all rank rows display count and percent.
- UI: donut and list appear only in All, list is complete/clickable; single scope updates all totals; 5/30/100 don't require re-fetch; scrolling stays internal; switching Top N resets that list's scroll to top.
- Navigation: each metadata type opens correct existing List search/filter, namespace preserved, returning to Stats restores view state and scroll positions.
- Reliability: rapid namespace selection doesn't show stale response; failed request yields explicit error/retry; refresh/navigation doesn't contaminate global option registries.
- Layout: 3 columns >=1200; 2 at 700–1199; 1 <700; widths based on content area; no visual changes other than color between light/dark.
- Regression: Shell sidebar persistence and existing List/Search/Detail flows remain intact.

## 5. Non-goals
- No new operational health cards, embedding charts, recall evaluation, growth rate or token statistics.
- No migration to a new frontend framework or global routing overhaul.
- No redesign of approved app modules or implementation of application-wide i18n in this change.
- No new backend feature unless a verified API gap is surfaced and explicitly approved.

## 6. Suggested Codex Plan Mode prompt

> Read `STATS_UI_DESIGN.md` and `STATS_UI_IMPLEMENTATION.md`. Inspect the current repository and the newly implemented Stats metadata API. Produce a detailed, reviewable Stats UI Coding Plan with exact files/functions, actual API mapping, state management, compatibility safeguards, drilldown integration, CSS approach, test plan and execution phases. Do not modify application code in Plan Mode. If any planned action changes the already approved Shell/Memory List/Search/Detail, or relies on an undocumented API behavior, report it as an open decision instead of silently making a choice.
