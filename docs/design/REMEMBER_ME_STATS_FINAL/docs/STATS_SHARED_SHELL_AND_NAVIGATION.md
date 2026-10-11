# Statistics — Shared Shell, View Switch & State (Authoritative)

Status: Confirmed UI decisions, 2026-10-10.

## Scope and constraints

- Retain current top header: `Remember Me · MCP Server`, existing collapsed/expanded sidebar, and original nav entries `Memories / Stats / 配置 / Audit` **without renaming**. i18n is future cross-app work, not part of this task.
- Do not redesign approved Memory List, Search/Filters, or Detail view/edit/create. Do not create additional sidebar item for Retrieval.
- `Statistics` is ONE page with two subviews: `Memory` and `Retrieval`.

## Exact shared top layout (CRITICAL)

Row 1: page title `Statistics` at left and `All Namespaces` selector at right.

Row 2: **fixed-position, fixed-width wide two-option segmented control** at left (`Memory | Retrieval`). On Retrieval only, a three-option time selector `7 Days | 30 Days | 90 Days` appears at right. On Memory the right-hand space stays EMPTY: do not center, resize, move, or stretch the Memory/Retrieval control. Do not animate lateral geometry across tabs.

Use identical left offset, width, height, radius, font metrics, padding and alignment of the `Memory/Retrieval` selector in BOTH subviews AND BOTH Light/Dark themes. Selection communicated via a muted grey filled segment, darker than the light-theme track and lighter than the dark-theme track; **not bright blue filled tab**. Dark theme uses corresponding slightly lighter charcoal fill. Exactly one selected view, with accessible tab/segmented semantics and visible keyboard focus. Prefer CSS variables/tokens over theme-specific layouts.

The time selector is hidden for Memory, but its absence MUST NOT change the geometry of the left switch. It is ONLY applicable to Retrieval. If container becomes narrow, stack view switch above time selector, with full touch targets; same behavior for both themes.

Do not add a separate `Retrieval Statistics` description card below the switch. A compact date/timezone/cutoff context line may precede Overview; no introductory card.

## Namespace and view behavior

- Namespace dropdown is common to both Memory and Retrieval, default `All Namespaces`. Preserve its selection when switching subviews. Do not overwrite global registered Namespace/Category candidates with scoped Stats data.
- Selecting a namespace refreshes current subview's data. In Memory, hide Namespace Distribution when one namespace is selected; the selected scope still governs other counters and metadata.
- Retrieval defaults to 7 Days and offers 7/30/90 Days. Time selector does not affect Memory.
- Restore current subview, namespace, Retrieval days, Memory Top-N and scroll positions only during in-app navigation using module-local state. Do not use sessionStorage or restore after F5. F5 retains the normal Memories entry; Stats preferences reset to Memory / All / 7 Days / Top 5. Always fetch fresh statistics.
- Each newly loaded page defaults to Memory / All Namespaces / 7 Days / Top 5. No Stats state is persisted between page loads.

## Theme equivalence

Light/Dark change ONLY color-related tokens: surfaces, typography colors, borders, semantic bars and charts. Geometry, component positions, dimensions, information, font sizing, iconography, chart type and behavior are the SAME across themes. Never build separate per-theme DOM layouts.

## Responsive

Implementation uses the existing viewport media-query system: row 2 stacks below 700 CSS px, the Retrieval Overview has 3 columns from 700px and 2 below, and the lower panels use 2 columns from 900px and 1 below. Validate actual content with the existing sidebar; controls remain operable and switch geometry stays consistent across view/theme at a given width.

## API boundaries

Memory view reads the existing completed metadata stats API. Retrieval view uses the separate accepted `GET /api/stats/retrieval` contract with namespace, days and browser IANA timezone. Do not consume `/api/audit` with a 500-event default limit to compute stats client-side. Respect API null and completeness semantics.

## Confirmed follow-up decisions

- Remove the local Stats Refresh button; use the existing toolbar Refresh for the active subview, retaining preferences.
- Shared Namespace options come from the existing global registry plus configured default, independent of loading Memory. Scoped responses never mutate global candidates.
- Data notes appear only when Debug is enabled in configuration (save then refresh the page). Diagnostic counts describe the global retained-audit scan. Ordinary mode still displays API errors and Retry.

User-approved label clarification: the Retrieval tab is displayed as **LLM Retrieval**, while internal view keys and API paths remain unchanged. Add the compact source caption **MCP search activity and reported feedback** beside the date/time context. This indicates the intended LLM retrieval use and the actual MCP data source; Admin search remains excluded.

2026-10-11 user decision: remove the standalone Keywords menu/page. Memories keyword filters and Stats keyword rankings/drilldown remain; MCP/API discovery is unchanged.
