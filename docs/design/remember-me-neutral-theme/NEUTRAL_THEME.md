# Remember Me — Neutral (Slate) Theme

**Status:** Approved design direction / implementation handoff  
**Scope:** Web UI visual theme only. No changes to information architecture, interactions, data structures, APIs, product copy, or backend.

## 1. Decision

Remember Me will have three **visual themes**:

- **Light** — existing bright theme, unchanged.
- **Neutral (Slate / 岩石灰)** — new soft slate-blue gray theme; the chosen third theme.
- **Dark** — existing dark theme, unchanged.

**System** remains a *preference mode*, not a fourth visual palette: follow operating-system Light or Dark. Neutral is selected explicitly. Keep the existing default theme behavior; do **not** silently change the initial default to Neutral.

**Not included now:** North African Mediterranean (Sand + White). Preserve the design as a possible future optional theme, without shipping it in the selector or implementing its tokens now.

### Design intent

Personal, calm, professional, technical, restrained, welcoming. The new theme is **not** the arithmetic midpoint between Light and Dark and is **not** a dark-gray / dimmed theme. It is a low-glare **light-family** palette: slate-tinted background, quiet blue-gray navigation, nearly white content cards, navy text, measured blue interaction cues.

## 2. Palette / semantic tokens

The hex values below are **proposed starting values**, to be validated against the existing UI in real screenshots. They should live in the application's existing token system. Do not layer a second unrelated styling system on top.

| Semantic role | CSS token example | Neutral value | Intended use |
|---|---|---|---|
| App canvas | `--rm-bg-app` | `#DCE3EA` | Whole app frame |
| Navigation/sidebar | `--rm-bg-sidebar` | `#D5DFE9` | Shell sidebar |
| Main workspace | `--rm-bg-workspace` | `#E8EDF2` | Content workspace behind cards |
| Surface | `--rm-bg-surface` | `#F2F5F8` | Cards, detail panes, inputs |
| Raised surface | `--rm-bg-raised` | `#F8FAFC` | Popovers, menus, dialogs |
| Subtle surface | `--rm-bg-subtle` | `#E4EBF2` | Inset groups, metadata tiles |
| Primary text | `--rm-text-primary` | `#22334A` | Titles, critical content |
| Secondary text | `--rm-text-secondary` | `#52657A` | Subtitles, metadata, hints |
| Accent | `--rm-accent` | `#205A9C` | Links, primary actions |
| Accent hover | `--rm-accent-hover` | `#174878` | Hovered primary action |
| Accent soft | `--rm-accent-soft` | `#D5E2F1` | Active navigation / selected fills |
| Outline | `--rm-border` | `#BAC7D4` | Inputs, cards, dividers |
| Quiet outline | `--rm-border-subtle` | `#CAD5DF` | Low-emphasis separators |
| Focus outline | `--rm-focus` | `#205A9C` | Keyboard focus |
| Destructive | `--rm-danger` | `#B42318` | Danger Zone, error actions |
| Disabled text | `--rm-text-disabled` | `#617185` | Only for truly disabled UI |

**Brand alignment:** The existing dark-navy sea-horse + M logo remains unchanged in geometry. Use it as the UI header mark for the Neutral theme; do not generate a new logo or make it sand-colored.

### Accessibility checks

- Body text on surface: `#22334A` on `#F2F5F8` ≈ **11.7:1**.
- Secondary text on surface: `#52657A` on `#F2F5F8` ≈ **5.5:1**.
- Accent text on surface: `#205A9C` on `#F2F5F8` ≈ **6.4:1**.
- Accent text on the slate sidebar background: `#205A9C` on `#D5DFE9` should also be checked in implementation.
- Check actual rendered disabled, placeholder, chart-label, focus, selected, danger and error states in both themes; don't rely on the rendered concept images for compliance.
- Focus states must be detectable by more than a subtle background shift.

## 3. Application guidance

### Shell and navigation
- Keep the existing layout, spacing, typography, iconography, and route names.
- Give sidebar a slightly darker slate tint than the workspace.
- Active navigation: a soft blue-slate fill **and** the existing shape/indicator convention. Hover should be distinguishable from active selection.
- The brand logo should appear only where already specified (e.g. top-left header); don't decorate each section with it.

### Memory List / Search / Detail
- Preserve current list-to-detail workflow, selection behavior and list click targets.
- Keep selected-memory affordance visually distinct from hover; retain the established selected marker (e.g. the leading vertical rail, if that is the current implementation).
- Search semantic / lexical / anchor choices and result display must remain as designed; no new filters or score labels introduced by a mockup.
- Detail panes use near-white surfaces to maximize readability; metadata tiles use subtle slate rather than saturated backgrounds.

### Stats
- Keep the existing Memory / Retrieval tab switching, time ranges and current back-end contract.
- Charts: navy / medium-blue primary series, muted blue-gray secondary series, readable axes and legends; avoid decorative gradients.

### Settings
- Add **Neutral** to the existing appearance choice alongside System, Light, Dark.
- Respect the previously agreed Settings layout (regular vs. maintenance tools, gated operations, danger grouping).
- No brightness sliders, custom accent color pickers, motion switches, account panels or other features simply because they appear in concept mockups.

### States and overlays
- Define normal, hover, active/selected, focused, disabled, error/danger states for input, button, card, tab, dropdown, dialog and tooltip.
- Visual distinction must survive zoom and small-screen sizing; preserve existing responsive behavior.
- Use a restrained shadow only when useful to separate elevated UI; rely mostly on surfaces and borders.

## 4. Theme-switching behavior

```text
Appearance preference  Visual palette
System                 OS light → Light; OS dark → Dark
Light                  Light
Neutral                Neutral (regardless of OS)
Dark                   Dark
```

- Preserve preference using the existing settings persistence mechanism.
- Switch in real time; no app reload necessary.
- `Neutral` should map to an explicit theme identifier such as `neutral`; labels may be localized.
- Expose all colors through semantic tokens; avoid scattershot hard-coded component overrides.
- Keep native control styling in the light family when Neutral is selected (`color-scheme: light`), so browser inputs don't unexpectedly become dark.

## 5. Visual references

The bundled screenshots are **color / material / hierarchy references only**. They are AI concept images and may contain routes, account features, data, labels, controls, layouts or interactions that are **not** part of the actual Remember Me product. **Do not implement any such features from the images.** The actual UI design decisions and production code take precedence.

- `refs/neutral_search.png` — list/detail and search emphasis.
- `refs/neutral_stats.png` — dashboards / charts.
- `refs/neutral_settings.png` — neutral tone across forms (not an authoritative Settings feature list).

## 6. Implementation boundaries and acceptance

**Suggested implementation steps:**
1. Inspect current theme infrastructure and inventory hard-coded colors, including icon libraries, CSS variables, charts and overlays.
2. Add the Neutral palette into existing semantic tokens; leave Light and Dark visually unchanged.
3. Add Neutral to theme selection and persistence; keep System's Light/Dark behavior.
4. Validate existing Memory List, Search, Detail, Stats, Settings, Help and maintenance mode views without changing their function.
5. Capture Light / Neutral / Dark comparisons and verify contrast, selection, hover, focus, disabled and error states.

**Acceptance:**
- Exactly three visual palettes are implemented: Light, Neutral, Dark; System remains an automatic mode.
- All existing pages remain functionally identical and visually coherent.
- Logo sizing and placement follow the existing branding decisions.
- No new features derived from mockups; no backend/API/schema changes.
- Neutral typography is comfortably legible; keyboard focus and selection remain evident.
- North African Mediterranean sand/white is documented only as a future candidate.

## 7. Future theme note — North African Mediterranean

A possible optional **Sand / White** palette would emphasize warm sand canvas, cream-white cards, deep-navy text and modest blue accents. If revisited, it must be **colors only**: no plants, tiles, arches, textures, ornaments or travel imagery. It should not increase complexity of the current implementation.
