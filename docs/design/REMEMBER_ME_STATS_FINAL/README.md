# Remember Me MCP — Statistics UI Final Design Package

Date: 2026-10-10
Status: Confirmed decisions implemented on 2026-10-10; targeted browser checks passed; accepted by the user and authorized for commit.

## Contents

- `docs/STATS_SHARED_SHELL_AND_NAVIGATION.md` — authoritative shared header, Memory/Retrieval switch, persistence, themes, responsive behavior.
- `docs/MEMORY_STATS_UI_DESIGN.md` — previously approved Memory Statistics design (unchanged).
- `docs/MEMORY_STATS_UI_IMPLEMENTATION.md` — previous Memory UI implementation guidance (reference; do not redo accepted work).
- `docs/RETRIEVAL_STATS_UI_DESIGN.md` — Retrieval-specific presentation rules.
- `docs/RETRIEVAL_STATS_UI_IMPLEMENTATION.md` — Codex implementation stages and validation.
- `docs/STATS_UI_ACCEPTANCE.md` — end-to-end test checklist and release gates.
- `samples/01_light_retrieval.png`, `02_light_memory.png`, `03_dark_retrieval.png`, `04_dark_memory.png` — four approved **composition references**, NOT accurate metric/field specifications.
- `samples/README.md` — mandatory notes on deviations in generated images.

## Priority of authority

1. Existing accepted Shell, Memory List, Search/Filters, Detail behavior and production code.
2. `STATS_SHARED_SHELL_AND_NAVIGATION.md` and this package's written requirements.
3. Memory and Retrieval UI design documents.
4. Images for spacing, density and theme cues only.

**IMPORTANT:** The generated images depict fake values and occasionally the WRONG metric definitions, rankings, count presets, icons, or trend deltas. Never implement these deviations. Memory page has five metrics; Retrieval has three; Feedback is -10..10 (NOT five-star); Memory Top-N presets are 5/30/100 (NOT 5/10/20); do not invent growth metrics or Memory Activity chart.

Backend Retrieval API is implemented in commit `7d36d2c`. The UI uses its accepted contract without backend changes.

Approved coding plan and implementation record: [53.4 Statistics dual view](../../project-management/plans/53.4-stats-retrieval-ui-coding-plan.md).

Confirmed follow-up decisions supersede earlier descriptions: no F5 state restoration; global toolbar Refresh only; Namespace candidates use the global registry plus configured default; Data notes is available only with server `--debug`.
