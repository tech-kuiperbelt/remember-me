# Retrieval Statistics UI — Codex Implementation Guidance

Status: Design approved, coding plan and final API adapter must be reviewed before implementation.

## Hard boundaries

- Frontend UI task only. Backend Retrieval API is under separate work; do not change Audit Search/Report behavior or schema here.
- Do NOT redesign accepted Shell, Memory List/Search/Filters, Detail or already accepted Memory Stats content.
- Preserve global stats.js / main.js initialization of Namespace and Category registries; scoped Stats data must never overwrite shared global lookup/candidate lists.
- Read `STATS_SHARED_SHELL_AND_NAVIGATION.md`, `RETRIEVAL_STATS_UI_DESIGN.md`, existing Memory design, plus the **actual** backend API implementation/response. If backend payload differs from design contract, surface discrepancy in plan rather than silently altering semantics.

## Staged plan to submit for approval

1. **Discovery/contract check (no edits):** inspect current stats DOM/JS/CSS, global initialization/candidate propagation, finished metadata API, new retrieval endpoint and error/null semantics. Map fields precisely.
2. **Shared view controls:** introduce Statistics Memory/Retrieval selection, shared namespace, Retrieval-only time presets. Keep switch dimensions/positions identical in both views/themes; hide time selector on Memory while leaving empty slot. Keep Memory component intact.
3. **Retrieval fetch + state:** request `/api/stats/retrieval?days=...&timezone=...` plus `namespace` iff selected; use browser IANA timezone and encode query. Latest-request-wins/AbortController to prevent stale responses after fast switching. Scope-scoped state only, not global registry mutation.
4. **Presentation:** one Overview panel, line chart, Search Modes, Feedback. Reuse existing components/tokens as possible; do not add heavyweight chart dependency without plan approval. Correct null/empty/error behavior.
5. **Navigation:** keep view, namespace, days and Memory preferences in module-local state only; navigation restores them with fresh data. F5 uses normal startup and resets Stats preferences. Do not use sessionStorage.
6. **Responsiveness/accessibility:** container-driven layouts, narrow control stacking, tooltip focus/touch, color contrast, no theme-specific geometry.
7. **Validation and review:** run existing tests and regression checks; compare with four samples while explicitly rejecting invented elements. Record any backend/schema mismatches and real API edge cases.

## Functional acceptance cases

- Memory→Retrieval→Memory: selected segmented control changes; **exact same x/y/width/height** of switch at each viewport, no content shift due to time control appearance/disappearance; namespace remains selected; Memory metrics unchanged.
- F5 while Retrieval/30 Days/specific namespace: normal Memories entry; reopening Stats shows Memory/All/7 Days/Top 5 with fresh data.
- New page load defaults Memory/All/7 Days/Top 5, including duplicated tabs.
- Search: success+failure counts in summary/trend/modes, zero bucket handling, and weekly bucket dates align to API.
- Feedback: deduplication/test exclusion server-owned, no client reinterpretation; percentages use rating_count, coverage uses successful nonempty searches, null coverage/rating shows em dash.
- No search vs no feedback vs read error show three distinct states.
- Namespace switch and rapid 7→30→90 updates cannot show out-of-order responses.
- Light/Dark same geometry; desktop/tablet/mobile layouts; keyboard and touch use.
- No regressions to Shell persistence, Memory List/Search/Detail, Stats Memory views, global Namespace/Category options, and existing Audit page.

## Avoid scope creep

No new sidebar navigation, no user account/header search, no extra Memory activity trend, no five-star ratings, no growth percentages, no recall accuracy headline, no persistent analytics DB, no caching or translation initiative.

## Confirmed follow-up decisions

- Remove the local Stats Refresh button; use the existing toolbar Refresh for the active subview, retaining preferences.
- Shared Namespace options come from the existing global registry plus configured default, independent of loading Memory. Scoped responses never mutate global candidates.
- Data notes appear only when launched with `--debug`, using the existing startup flag. Diagnostic counts describe the global retained-audit scan. Ordinary mode still displays API errors and Retry.
