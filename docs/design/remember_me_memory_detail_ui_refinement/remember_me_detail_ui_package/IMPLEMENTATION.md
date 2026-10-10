# Codex implementation plan — Memory Detail UI refinement

## Scope guardrails
**ONLY** Memory Detail Drawer shared UI for View/Edit/Create, associated styles and narrowly required interaction tests. Do NOT change Memory List Card, Search/Filters, Shell, routes, backend APIs, persistence schema, retrieval logic, data values, existing Markdown semantics, Debug/More/Copy draft business behavior. Do not commit or restart services unless user explicitly asks. Work in the actual repo; first identify existing components and tests rather than assuming filenames.

## Phase 0 — Inspect and baseline
- Locate current Drawer View/Edit/Create components, Header, Markdown editor/preview, metadata pickers, technical details, unsaved-draft logic and existing tests.
- Read `UI_DESIGN.md` and compare `reference/current_*.png` with `reference/approved_mockup.png`.
- Map existing behavior (including close by outside click), validation, copy buttons, save states and keyboard event handlers.
- Distinguish features **already implemented** vs absent. Preserve existing correct behavior. If design conflicts with actual business validation, flag conflict before changing it.

## Phase 1 — Shared structure & View
- Keep current fixed Drawer width; sticky header and scrollable body. Header title truncation and existing operations with appropriate button hierarchy.
- Polish View reading typography, tag semantic styles, empty-value display, spacing; reuse Markdown renderer and existing content. Confirm long Summary and CJK text do not overlap buttons.
- Maintain List context when opening and closing Drawer.

## Phase 2 — Technical details
- Default-collapsed section: aligned key/value rows and inline Copy ID.
- ISO hover plus readable local time; preserve original raw values.
- 240px max read-only syntax-highlighted JSON box, copy full payload, scroll for long data; use lightweight local rendering, no editor dependency.
- Verify Light and Dark.

## Phase 3 — Edit/Create form
- Auto-resize Summary 2–5 lines; Content editor 240–500px; keep tabs, Markdown and draft intact on tab switch.
- Metadata controls: Enter/Add for categories, keywords, anchors; isolate Enter from form submission, preserve established anchors backend semantics.
- Do not provide raw JSON editing in Edit or Create. Remove Create Other metadata input; preserve existing extra metadata through the unchanged update contract and show it read-only in Technical details.
- Defer initial required errors until touched/blurred or submit; respect backend schema and error rules.

## Phase 4 — Lifecycle and dismissal
- Save busy and disable duplicate submit; success to View; error retains form.
- Dirty check on meaningful field edits only; `×`, Esc, outside click, Cancel (and any other navigate-away triggers) route through same confirm. Busy save disallows dismissal.
- Outside click must not activate underlying list item. Test focus behavior and restoring List position.

## Phase 5 — Verification & report
Test at minimum:
1. View/Edit/Create layouts at typical desktop width; long CJK + English summaries, scroll.
2. Light/Dark, text contrast, hover/focus/keyboard.
3. Header stays fixed with long Content, all actions accessible.
4. Edit/Preview preserve draft, Markdown rendering equivalence, caret/scroll best effort.
5. Summary auto-resize boundaries 2 and 5 lines; Content 240/500 boundaries and scrolling.
6. Enter/Add for tag editors; duplicate, whitespace and anchor literal edge cases; Enter never submits form.
7. Create initially clean; blur/submit errors, corrected field clears; server failure retains data; neither Edit nor Create exposes a JSON input.
8. Close by ×/Esc/outside click, dirty & clean drafts, saving busy, cancel confirmation; click another card behind overlay does not open it.
9. Technical details time hover, Copy ID/JSON, huge JSON scroll, formatted raw data unaffected.
10. No regressions in List/Search/Debug/More/Copy draft or any existing API logic.

## Acceptance criteria
- All confirmed `UI_DESIGN.md` behaviors, or a documented list of conflicts/issues requiring user decision.
- Existing functions and records intact; no unrequested backend refactoring.
- Existing tests remain green; add targeted regression coverage as practical; provide precise test commands/results and changed-file list.
- Avoid claiming browser manual validation unless actually performed.

## Suggested instruction to Codex
"Implement only the Memory Detail Drawer UI refinement in this package. Treat UI_DESIGN.md as confirmed rules and the screenshots as visual references, not code contracts. First inspect the current implementation and outline files / behaviors, then implement in phases, run targeted tests, and report differences. Do not modify Memory List/Search, backend interfaces or persistence behavior. Don't commit or restart production services without explicit permission."
