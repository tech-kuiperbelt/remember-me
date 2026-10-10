# Remember Me MCP — Retrieval Statistics API Design

**Status:** Design approved at product-contract level; aligned with 53.3 Coding Plan; implementation pending  
**Date:** 2026-10-10  
**Scope:** Read-only Admin API for the Retrieval view of Statistics. Does not change MCP tool behavior, audit-writing format, or the existing Memory Statistics implementation.  
**Revision (2026-10-10):** aligned with `docs/project-management/plans/53.3-retrieval-stats-api-coding-plan.md`: eligibility is capability-based (no fixed schema-transition date), `context.source` is restricted to `mcp`, a missing timezone database returns 503, and the feedback dedup tie-break is defined by scan order.

## 1. Goals and boundaries

Add `GET /api/stats/retrieval` to expose reliable *usage and explicit feedback* statistics from retained Audit JSONL files. UI has two Statistics views: `Memory` (already designed; unchanged) and `Retrieval` (this API). Both share the Namespace filter; only Retrieval has time presets `7 / 30 / 90 Days`.

**Non-goals:** true recall/precision, Hit@K, model evaluation, latency monitoring, storage health diagnostics, historical audit migration, schema changes, a statistics database, or caching in v1. Do not describe feedback metrics as satisfaction or recall accuracy.

## 2. Request contract

```http
GET /api/stats/retrieval?days=7&timezone=Asia%2FShanghai&namespace=remember-me
```

| Parameter | Type | Rule |
|---|---|---|
| `days` | integer | Optional; one of `7`, `30`, `90`; default `7`. Reject other values. |
| `timezone` | IANA timezone string | Supplied by browser (`Intl.DateTimeFormat().resolvedOptions().timeZone`); required in v1. Reject unrecognized values (HTTP 400 `invalid_timezone`), no silent fallback. If the server has no timezone database, return HTTP 503 `timezone_database_unavailable`; never substitute UTC. |
| `namespace` | string | Optional; omitted = all namespaces. If supplied, use the existing namespace validation rules. Never treat empty/blank as All by accident. |

The response must declare the resolved scope and time window. Use proper Admin error responses for invalid parameters; do not return a successful zero result for a validation failure.

### Calendar-day window

Interpret N days as **today plus N−1 prior calendar dates** in the supplied timezone. Include events from local midnight of the first date through the evaluation instant (`window_end_utc`); today is partial and counted live. Use timezone-aware calculations, not `N * 24h`: DST can change elapsed hours. Return UTC bounds as ISO-8601 instants and local start/end dates. Record a single evaluation instant for an entire request.

Daily trend buckets: 7 or 30 local calendar dates, including zero-search dates. For 90 days use weekly buckets with explicit local/UTC start/end bounds, including partial edge weeks and today; select a deterministic week convention (proposed ISO Monday) in Coding Plan and test it. Bucket totals must sum to Search Count.

## 3. Audit scope and event eligibility

- Read current `audit.jsonl` and retained rotated files (`.1` ... `.9`) on the server. Do **not** use `/api/audit` with a capped `limit` as the data source.
- Apply **capability-based eligibility**, not a fixed schema-transition date. An event counts toward call volume only when it has a valid timezone-aware timestamp, a recognized search tool name, and an `ok`/`error` status; `search_id` is a per-metric requirement, not a global gate. Historical records are incorporated to the extent their fields allow; nothing is backfilled.
- Include only `context.source == "mcp"` search records. Records without a recognizable source are skipped and counted in `data_quality`.
- Include search tools `search_semantic`, `search_lexical`, `search_anchor`, `search_meta` (other tools are not searches).
- Search counts/trends/mode distribution include both `ok` and `error` search invocations. Successful search = `status=ok`; nonempty successful search = successful search with nonempty `result.items`.
- Reports are analyzed only when `status=ok`, with valid `search_id`, a valid per-Memory rating, and a matching successful search that returned the rated Memory ID. Failed/rejected report events are excluded.
- Omit report entries whose `feedback_context.analysis_group` explicitly equals `declared_test`, **before** deduplication. Include `declared_llm_task`, `unclassified`, and absent group, with the caveat that unmarked data are not proven non-test. Verify the exact location of `feedback_context` in code and audit examples before implementing.
- `report.namespace` is not usable (null): scope each report by joining its `search_id` to the associated search event, then using **search.namespace**.
- Time and namespace selection apply to **search events**. Include qualifying reports even if reported after the search day or the selected window. In v1, reports are limited to those still present in retained audit history, so historical aggregates can change as events are appended or rotated.
- Deduplicate feedback by `(search_id, memory_id)`: last valid rating wins, by event timestamp, with a deterministic tie-breaker: scan order is oldest rotated file to newest, then file order within each file, and the last valid rating encountered wins. Different searches referring to the same Memory remain independent. A search with one or more valid, deduplicated ratings counts once as a rated search.

If the matching search no longer exists because it was rotated out, do not infer its namespace or time from the report. Exclude it from scope-based quality aggregates and record a diagnostic count. `unmatched_reports` counts only reports whose `search_id` is absent from all retained search events; a retained search that merely falls outside the selected namespace/window is excluded silently and is **not** an unmatched report.

## 4. Metric definitions

All metrics refer to the selected namespace and search-time window, unless marked otherwise.

| Metric | Definition |
|---|---|
| `total_searches` | Count of eligible search events including failed calls. |
| `successful_searches` | Count of eligible searches with `status=ok`. |
| `failed_searches` | Count of eligible searches with `status=error`. |
| `non_empty_searches` | Successful searches with at least one result item. |
| `search.modes[]` | Calls by search tool mode, **including errors**; always return semantic, lexical, anchor, meta even at zero. |
| `search.trend[]` | Calls by local date (7/30) or explicit local week bucket (90), including errors and zero buckets. |
| `rated_searches` | Distinct successful, nonempty `search_id` with ≥1 qualifying, deduplicated rating. |
| `eligible_searches` | `non_empty_searches`. |
| `feedback.coverage_percent` | `100 * rated_searches / non_empty_searches`. If denominator is 0, return `null`. |
| `rating_count` | Number of deduplicated valid `(search_id, memory_id)` ratings. |
| `average_rating` | Arithmetic mean of those ratings, or `null` when none exist. |
| `rating_distribution` | Positive >0, neutral =0, negative <0. Percentages if exposed use `rating_count` as denominator; null if zero. |

Do **not** interpret empty-result rate as recall, nor positive feedback rate as satisfaction of all searches. Neither positive percentage nor Hit@K is an overall recall measure. Unrated results are not silently rated zero.

## 5. Proposed response JSON

**All numbers below are illustrative and deliberately not derived from one historical window.** Coding Plan may propose naming adjustments, but should preserve the semantics and data completeness.

```json
{
  "scope": {
    "namespace": null,
    "days": 7,
    "timezone": "Asia/Shanghai",
    "local_start_date": "2026-10-04",
    "local_end_date": "2026-10-10",
    "window_start_utc": "2026-10-03T16:00:00Z",
    "window_end_utc": "2026-10-10T07:32:00Z",
    "available_from": "2026-10-08T11:02:00Z",
    "coverage_complete": null
  },
  "search": {
    "total_searches": 80,
    "successful_searches": 78,
    "failed_searches": 2,
    "non_empty_searches": 70,
    "modes": [
      {"mode": "semantic", "count": 40},
      {"mode": "lexical", "count": 22},
      {"mode": "anchor", "count": 10},
      {"mode": "meta", "count": 8}
    ],
    "trend": [
      {"date": "2026-10-04", "count": 9},
      {"date": "2026-10-05", "count": 0}
    ]
  },
  "feedback": {
    "eligible_searches": 70,
    "rated_searches": 24,
    "coverage_percent": 34.29,
    "rating_count": 48,
    "average_rating": 5.6,
    "distribution": {"positive": 40, "neutral": 8, "negative": 0}
  },
  "data_quality": {
    "skipped_malformed_lines": 0,
    "skipped_unrecognized_source": 0,
    "excluded_test_reports": 0,
    "unmatched_reports": 0,
    "conflicting_search_ids": 0,
    "warnings": []
  }
}
```

In an actual 7-day response, `trend` MUST contain seven entries. In a 30-day response it MUST contain 30. For 90-day responses provide a different explicit weekly-bucket shape (including `start_date`, `end_date`, UTC bounds and count); finalize precise field names in review, but do not leave bucket semantics unspecified.

### Data coverage semantics

`available_from` denotes the earliest *retained, eligible* search-event timestamp usable for call counting, or `null` when none is retained. It is not proof that earlier records never existed, so it must not be used as a hard lower bound. `coverage_complete` is tri-state: `true` only with defensible evidence of full coverage, `false` only with defensible evidence of loss, otherwise `null` (v1 returns `null`). Do not infer incompleteness solely because the first event occurred after the requested start date. Surface corrupt/skipped records in `data_quality`; UI must not silently treat a partial read as clean zero activity.

## 6. Failure and empty-data contract

| Case | Behavior |
|---|---|
| No searches in window | HTTP 200. All search counts zero; appropriate trend buckets all zero; feedback coverage `null`, average rating `null`, rating count zero. |
| Searches exist but no valid feedback | HTTP 200. `rated_searches=0`, `coverage_percent=0` **if `non_empty_searches>0`**, and `average_rating=null`. |
| Search results all empty | HTTP 200. Feedback denominator is zero, so coverage `null`. |
| Invalid days/timezone/namespace | Explicit parameter error (reuse Admin error conventions). |
| Server lacks a timezone database | HTTP 503 `timezone_database_unavailable`; never silently substitute UTC. |
| A few malformed JSONL lines | Skip with warning and count; return readable subset, mark data as potentially incomplete. |
| Audit file cannot be read | Explicit non-200 error; never misrepresent as empty successful statistics. |
| Missing/removed rotated records | Aggregate only retained history and expose uncertain coverage; do not reconstruct missing data. |

Important distinction: **0% coverage means eligible nonempty searches exist but none were rated. `null` means coverage is undefined because the denominator is zero.**

## 7. Implementation constraints

1. Add the narrow, read-only Admin endpoint and a dedicated aggregation service over audit files. Keep existing `/api/audit`, `/api/stats`, `/api/stats/metadata`, MCP tools/resources and Search/Report workflows unchanged.
2. Do not add caching, statistics tables, indexes, migrations or extra dependencies in v1. Avoid work on the event loop where a file scan would block it. Do not hold an Audit writer lock for a prolonged full-file scan.
3. Read JSONL defensively. Consider files being appended/rotated during the scan; do not claim snapshot consistency if unavailable. Choose and document predictable handling (e.g. stable file snapshot/ bounded scan and diagnostic warning).
4. Perform grouping and deduplication on the server. Client requests one JSON aggregate, does not download raw Audit records.
5. UI Memory/ Retrieval views share namespace selection; this API returns only Retrieval metrics. Retrieval alone owns 7/30/90 days and timezone.
6. Minimize disclosure: return counts, aggregates, time bounds, and data-quality metadata; do not expose audit query texts, summaries, or other raw sensitive tool payloads through this stats endpoint.
7. Test response contract and correctness before frontend wiring. Measure performance using representative files up to the current retention cap; only reconsider cache/preaggregation if measurements justify it.

## 8. Acceptance tests

- `days` in {7,30,90}, invalid values; valid/invalid IANA timezone; DST spring and autumn transitions; local midnight boundaries and current partial day.
- All namespaces and one namespace; report events have null namespace but join correctly by search ID.
- Modes including zero counts; failed searches included in volume/trend, excluded from feedback denominator; zero buckets and sum-to-total invariant.
- Search starts inside window / report arrives after it, attributed to search date and namespace.
- Multiple reports for same `(search_id, memory_id)`, latest **valid non-test** rating wins; separate searches independent.
- Neutral 0 counts as a valid rating; missing rating does not. Failed/rejected report excluded. Declared-test excluded; absent/unclassified included.
- Nonempty denominator 0 -> null feedback coverage; nonempty denominator >0 with no ratings -> 0%; no ratings -> null average.
- Old-schema events are excluded; search/report IDs cannot be correlated across missing history; corrupt lines counted; unreadable audit files return errors.
- Audit rotation and concurrent append behavior documented and verified; `available_from` and tri-state completeness not overclaimed.
- Request with 90 days returns consistent weekly bins; partial boundary weeks labeled and counted once.
- No breaking changes to existing Admin/MCP APIs or the previously approved Memory Stats UI.

## 9. Coding Plan review checklist

Before coding, Codex/Astra should: (a) verify the exact audit JSON shapes and `feedback_context` location (**verified**: `result.feedback_context.analysis_group`, present from 2026-09-30 onward; earlier successful reports are included as unclassified), (b) choose precise week-bucket fields and deterministic timestamp tie-break rules, (c) design safe read/rotation handling, (d) confirm Admin error conventions and namespace validation, (e) locate tests and build synthetic JSONL fixtures, and (f) identify uncertain definitions explicitly instead of silently changing the agreed product semantics.

**Next:** Enter Codex Plan Mode using this document as the functional contract; review the Coding Plan before authorizing changes.
