# Memory Detail Drawer — UI Specification

Updated 2026-10-08 to match the implemented UI. [50.6.2](../../project-management/plans/50.6.2-memory-detail-frontend.md) defines the approved behavior; [50.6.3](../../project-management/plans/50.6.3-memory-detail-coding-plan.md) records modules and verification. Reference images remain visual references; this text and the API contract take precedence.

## Surface and hierarchy

The detail surface is a modal drawer fixed to the right, 60vw wide clamped to 640–1040px and at most 100vw, filling the viewport height. This responsive width follows the user's wide-screen review. The header stays fixed; the body scrolls independently. The visible background is dimmed and inert. Its scrollbar and list width remain unchanged.

The information order is Header → Summary → Content → Categories → Keywords → Anchors → Technical details. Opening or closing preserves the list query, filters, sort, page and scroll. Selection follows memory ID, never row position.

The header shows a title derived from the existing summary/body, Namespace and updated time. There is no stored title field. The title is ellipsized with its full value available on hover.

View actions are Edit, More and Close. More contains Copy content and Delete. Edit actions are Cancel, Save changes, Copy draft and Close. Save is disabled when clean, invalid or busy. Cancel returns to the last confirmed View without another discard confirmation.

## Summary and Content

Summary shows the complete stored value as plain text, preserving line breaks. A null/empty summary shows “无摘要”; it is not replaced by the list's derived display summary.

Content renders Markdown in View. Edit uses the raw source with Edit/Preview controls; Preview shows the unsaved draft and does not save. Switching preserves exact source, selection and editor scroll.

Markdown supports headings, paragraphs, lists, quotes, inline/fenced code, tables, rules and links. Code uses a copy button and local horizontal scrolling; tables scroll locally. Long text and URLs wrap. Rendering uses existing theme tokens and does not place the entire body in a gray card.

Raw HTML is escaped before allowlist sanitization. No scripts, frames, forms, SVG/MathML, event attributes, inline styles or content-supplied IDs/names are accepted. Links allow only absolute HTTP/HTTPS and mailto; web links open with `noopener noreferrer`. Relative, protocol-relative and other URLs become text. Images become alt text and a safe link: there is no automatic image request. Rendering failure shows an inline error and faithful source fallback.

Marked 18.1.0 and DOMPurify 3.4.16 are local, fixed releases. Package sources, integrity values and licenses are recorded in [vendor README](../../../web/static/vendor/README.md).

## Categories, Keywords and Anchors

View displays all Categories, five Keywords with inline +N expansion/collapse, and all Anchors preserving case and order. Each dimension has its own heading and stable IDs; long values wrap.

Edit uses the shared category/keyword picker with independent instance IDs, request state and a popup inside the drawer. Categories allow at most three values, including explicit new values. Keywords have no write cap; literal candidate search uses 250ms debounce and pages of 20, rather than loading the vocabulary. The list filter retains its own 20-value limit.

Namespace is an editable input with known-value suggestions. Unknown namespaces remain editable and use the existing save confirmation; candidate queries do not target an uncreated namespace. Changing draft Namespace/Categories invalidates old keyword responses without losing selections.

Anchors use a separate add/remove input, at most five values, each 1–128 Unicode code points after NFKC and whitespace normalization. ASCII duplicates are case-insensitive; non-ASCII duplicates are exact. Body changes prompt the user to check anchor accuracy.

Unmodified historical metadata is preserved. Invalid or over-limit selected values remain visible and removable; explicitly edited dimensions require correction before saving.

## Technical details and accessibility

Technical details is collapsed by default. It shows full ID, created/updated timestamps and read-only metadata JSON, plus Copy ID and the shared Debug toggle. It does not show embedding, Score, Source, tabs or invented character quotas.

The drawer has dialog semantics and stable detail-* region IDs. Loading/error states retain reachable close/retry actions. Opening focuses Close; editing focuses Namespace. Tab stays within the topmost active surface, excluding controls inside closed details. Escape closes the picker/menu/confirmation first, then handles drawer leave; IME composition is ignored.

Dirty close, overlay click and programmatic navigation use the same Keep editing / Discard changes confirmation. Background rows cannot be clicked while the modal is open; close first to inspect another row. Saving/deleting locks controls and leave actions. Closing restores the original row focus, or the result bar if that row is absent.

Debug shares state with the shell button, does not alter layout or execute business actions, and exits to a reachable drawer control.

## Save and delete presentation

PUT success marks the draft saved, refreshes list/stats once and starts authoritative GET. Successful GET returns to View with the drawer open. GET failure shows “已保存，详情刷新失败” in a read-only state; Retry only repeats GET. GET 404 shows a missing-record state.

PUT failure retains the draft and shows an inline error. Body changes submit current Summary and Anchors even when those fields were not edited; blank summary is `""`, empty anchors are `[]`. An original null summary stays null after a body edit rather than deriving a stored summary.

Delete is available only in View under More. Confirmation shows the derived title and full ID, defaults to Cancel, and disables repeat submission. Failure retains the confirmation and record; success closes the drawer and refreshes the active result set once.

List membership and ranking always come from re-running the current Browse/Semantic/Lexical/Anchor request. Absence from the current page is not evidence that a memory stopped matching. Page and drafts are retained; an invalid final page is corrected by the data module. Summary clearing updates updated_at and can move a Browse record upward normally.
