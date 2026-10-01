# Remember Me - Memories UI Design Package

This package captures the current agreed UI scope for the **Memories** workspace.

## Current scope

Included now:
- Application shell integration for Memories
- Header search
- Namespace / Categories / Keywords filters
- Automatic List API / Search API switching
- Memory list presentation
- Sort and pagination guidance
- Loading / empty / error states

Explicitly deferred:
- Memory Detail layout
- Edit flow
- Delete flow and confirmation
- History / audit details inside a memory

The list should keep a future detail entry point, but this phase must not define or implement the detail workflow beyond a placeholder/navigation hook.

## Files

- `UI_MEMORY_SPEC.md` - primary UI and interaction specification
- `UI_MEMORY_LIST_SPEC.md` - detailed list-item and list-state specification
- `UI_MEMORY_IMPLEMENTATION_PLAN.md` - phased implementation advice for Codex
- `images/01-memories-search-list-overview.png` - overall Search + Filter + List concept
- `images/02-filter-interaction.png` - Category / Keyword filter interaction reference
- `images/03-list-item-design.png` - list information hierarchy reference

## Core product decision

**Memories is one workspace, not separate List and Search pages.**

- Empty search query -> List API
- Submitted non-empty query -> Search API
- Clearing the query -> return to List API
- Filters apply in both modes
