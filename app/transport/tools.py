"""MCP tool orchestration: one table for definitions, handlers and policies.

``TOOL_SPECS`` is the single source of truth for ``tools/list`` and for tool
dispatch. Tool schema/description data lives in :mod:`app.transport.tool_definitions`; the
transport lifecycle (SSE / Streamable HTTP / stdio) and admission stay in
:mod:`app.server`. Each handler is a plain function that adapts arguments and
calls the synchronous service; response shaping, timing, exception mapping,
search receipts and audit are shared here.
"""

import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

import mcp.types as types

from app.domain.anchors import InvalidAnchors
from app.domain.categories import MAX_CATEGORIES
from app.embedding.embed import EmbeddingError, EmbeddingInputTooLong
from app.domain.lexical import LexicalQueryError
from app.domain.namespace_policy import NamespaceNotFound, NAMESPACE_NOT_FOUND
from app.transport.report_feedback import InvalidReport, analysis_group
from app.service import _prepare_for_llm
from app.transport.tool_definitions import build_tools

logger = logging.getLogger("server")

_DEFINITIONS = {tool.name: tool for tool in build_tools()}


@dataclass
class ToolContext:
    """Per-call context: the service plus transport-owned collaborators."""

    service: object
    audit: object
    receipts: object
    runtime: object = None
    context: dict = field(default_factory=dict)


@dataclass
class ToolOutcome:
    data: dict
    memory_refs: list = field(default_factory=list)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    definition: types.Tool
    handler: Callable[[ToolContext, dict], ToolOutcome]
    hint: Callable[[dict, dict], str]
    write: bool = False
    search: bool = False
    namespace_mode: str = "none"      # search | write | keywords | none


# ---------------------------------------------------------------------------
# Argument adapters — plain functions, one per tool.
# ---------------------------------------------------------------------------

def _handle_add(ctx, a):
    data = ctx.service.memory_add(a["text"], a.get("metadata"), a.get("namespace"),
                                  a.get("summary"), a.get("confirm_codes"))
    refs = []
    if data.get("id"):
        refs = [{"id": data["id"],
                 "namespace": a.get("namespace") or ctx.service.settings.DEFAULT_NAMESPACE}]
    return ToolOutcome(data, refs)


def _handle_search_semantic(ctx, a):
    result = ctx.service.memory_search(
        a["query"], int(a.get("k", 5)), float(a.get("threshold", 0.0)),
        a.get("namespace"), keywords=a.get("keywords"),
        categories=a.get("categories"), return_resolution=True,
        updated_after=a.get("updated_after"), updated_before=a.get("updated_before"))
    result["items"] = _prepare_for_llm(result["items"])
    return ToolOutcome(_wrap_search_semantic(result))


def _handle_search_lexical(ctx, a):
    items = _prepare_for_llm(ctx.service.memory_search_lexical(
        a["query"], int(a.get("k", 10)), a.get("namespace")))
    return ToolOutcome(_wrap_search_lexical(items))


def _handle_search_anchor(ctx, a):
    items = _prepare_for_llm(ctx.service.memory_search_anchor(
        a["query"], int(a.get("k", 10)), a.get("namespace")))
    return ToolOutcome(_wrap_search_anchor(items))


def _handle_search_meta(ctx, a):
    ns = a.get("namespace")
    limit = int(a.get("limit", 100))
    offset = int(a.get("offset", 0))
    sort_by = a.get("sort_by", "created_at")
    items = _prepare_for_llm(ctx.service.memory_list(
        ns, limit, offset, sort_by, a.get("updated_after"), a.get("updated_before"),
        keywords=a.get("keywords"), categories=a.get("categories")))
    return ToolOutcome(_wrap_search_meta(items, ns, limit, offset, sort_by))


def _handle_get_details(ctx, a):
    data = ctx.service.memory_get_details(a["memory_ids"])
    refs = [{"id": item.get("id"), "namespace": item.get("namespace")}
            for item in data.get("found", [])[:100]]
    return ToolOutcome(data, refs)


def _handle_update(ctx, a):
    old = ctx.service.storage.get(a["memory_id"])
    data = ctx.service.memory_update(a["memory_id"], a.get("text"), a.get("metadata"),
                                     a.get("namespace"), a.get("summary"),
                                     a.get("confirm_codes"))
    new = ctx.service.storage.get(a["memory_id"])
    refs = [{"id": a["memory_id"],
             "namespace_before": old.get("namespace") if old else None,
             "namespace": new.get("namespace") if new else None}]
    return ToolOutcome(data, refs)


def _handle_delete(ctx, a):
    return ToolOutcome(ctx.service.memory_delete(a["memory_id"]))


def _handle_list_keywords(ctx, a):
    data = ctx.service.memory_list_keywords(**a)
    data["keyword_query_id"] = "kq_" + secrets.token_hex(8)
    return ToolOutcome(data)


def _handle_stats(ctx, a):
    return ToolOutcome(_wrap_stats(ctx.service.memory_stats()))


def _handle_report(ctx, a):
    receipt = ctx.receipts.validate(
        a.get("search_id"), a.get("ratings"),
        a.get("feedback_source"), a.get("feedback_purpose"))
    data = ctx.service.memory_report(a["search_id"], a["ratings"])
    data["feedback_context"] = {
        "declared_source": a.get("feedback_source") or "unspecified",
        "declared_purpose": a.get("feedback_purpose") or "unspecified",
        "declaration_trust": "caller_declared",
        "analysis_group": analysis_group(a.get("feedback_source"),
                                         a.get("feedback_purpose")),
        "search_tool": receipt.tool,
        "search_namespace": receipt.namespace,
        "search_request_id": receipt.request_id,
        "search_transport": receipt.transport,
        "result_membership": "validated",
    }
    return ToolOutcome(data)


def _hint(name: str) -> Callable[[dict, dict], str]:
    """Bind a tool's next-step guidance; ``_result_hint`` is resolved at call time."""
    def _bound(arguments: dict, data: dict) -> str:
        return _result_hint(name, arguments, data)
    return _bound


# ---------------------------------------------------------------------------
# The table: definition + handler + read/write + receipt/namespace mode + audit.
# ---------------------------------------------------------------------------

TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(name="list_keywords", definition=_DEFINITIONS["list_keywords"],
             handler=_handle_list_keywords, hint=_hint("list_keywords"),
             namespace_mode="keywords"),
    ToolSpec(name="add", definition=_DEFINITIONS["add"],
             handler=_handle_add, hint=_hint("add"),
             write=True, namespace_mode="write"),
    ToolSpec(name="search_semantic", definition=_DEFINITIONS["search_semantic"],
             handler=_handle_search_semantic, hint=_hint("search_semantic"),
             search=True, namespace_mode="search"),
    ToolSpec(name="search_lexical", definition=_DEFINITIONS["search_lexical"],
             handler=_handle_search_lexical, hint=_hint("search_lexical"),
             search=True, namespace_mode="search"),
    ToolSpec(name="search_anchor", definition=_DEFINITIONS["search_anchor"],
             handler=_handle_search_anchor, hint=_hint("search_anchor"),
             search=True, namespace_mode="search"),
    ToolSpec(name="search_meta", definition=_DEFINITIONS["search_meta"],
             handler=_handle_search_meta, hint=_hint("search_meta"),
             search=True, namespace_mode="search"),
    ToolSpec(name="get_details", definition=_DEFINITIONS["get_details"],
             handler=_handle_get_details, hint=_hint("get_details"),
             namespace_mode="none"),
    ToolSpec(name="update", definition=_DEFINITIONS["update"],
             handler=_handle_update, hint=_hint("update"),
             write=True, namespace_mode="write"),
    ToolSpec(name="delete", definition=_DEFINITIONS["delete"],
             handler=_handle_delete, hint=_hint("delete"),
             write=True, namespace_mode="none"),
    ToolSpec(name="stats", definition=_DEFINITIONS["stats"],
             handler=_handle_stats, hint=_hint("stats"),
             namespace_mode="none"),
    ToolSpec(name="report", definition=_DEFINITIONS["report"],
             handler=_handle_report, hint=_hint("report"),
             namespace_mode="none"),
)

_SPECS_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


def list_tool_definitions() -> list[types.Tool]:
    return [spec.definition for spec in TOOL_SPECS]


def tool_spec_for(name: str) -> ToolSpec | None:
    return _SPECS_BY_NAME.get(name)


def execute_tool(ctx: ToolContext, name: str, arguments: dict) -> types.CallToolResult:
    """Shared outer layer: state check, dispatch, receipt, hint, audit, errors."""
    logger.info("Tool call: %s %s", name, arguments)
    start = time.perf_counter()
    memory_refs = []
    try:
        if ctx.runtime is not None and ctx.runtime.state != "running":
            raise RuntimeError("service is closing; no new work accepted")
        spec = _SPECS_BY_NAME.get(name)
        if spec is None:
            raise ValueError(f"Unknown tool: {name}")
        outcome = spec.handler(ctx, arguments)
        data = outcome.data
        memory_refs = outcome.memory_refs
        if spec.search and data.get("items"):
            ctx.receipts.remember(data["search_id"], name, data["items"],
                                  arguments.get("namespace"), ctx.context)
        hint = spec.hint(arguments, data)
        ctx.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                         context=ctx.context, memory_refs=memory_refs)
        return _ok(data, hint)
    except NamespaceNotFound as e:
        # Not an MCP error: return a structured result so the LLM can read the
        # available namespaces and retry with confirm_codes when intended.
        data, hint = _namespace_error_result(e, search=spec.namespace_mode == "search")
        if spec.namespace_mode == "keywords":
            data.update({"namespace": e.requested, "mode": arguments.get("mode", "browse"),
                         "results": []})
            hint = "Unknown namespace; choose a known namespace from response_meta or omit namespace."
        ctx.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                         context=ctx.context)
        return _ok(data, hint)
    except (EmbeddingError, RuntimeError) as exc:
        if isinstance(exc, EmbeddingInputTooLong):
            code, hint = "embedding_input_too_long", "Shorten the input and retry."
        elif isinstance(exc, EmbeddingError):
            code, hint = "embedding_unavailable", "Check the configured embedding provider, then retry."
        elif ctx.runtime is not None and ctx.runtime.state != "running":
            code, hint = "service_closing", "Reconnect after the service restarts."
        else:
            ctx.audit.record(name, arguments, None, (time.perf_counter() - start) * 1000,
                             error={"type": type(exc).__name__, "message": str(exc)},
                             context=ctx.context)
            raise
        data = {"response_code": code, "detail": str(exc)}
        ctx.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                         error={"type": type(exc).__name__, "message": str(exc)},
                         response_code=code, context=ctx.context)
        return _ok(data, hint)
    except InvalidAnchors as exc:
        # Caller anchors failed strict validation: rejected, never coerced to [].
        code = "invalid_anchors"
        data = {"response_code": code, "detail": str(exc)}
        hint = ("Fix metadata.anchors (0-5 distinct strings, each non-empty and at most "
                "128 characters) and retry. Pass [] for no anchors. On update, omitting "
                "anchors preserves them only when the text is unchanged or whitespace-equivalent; "
                "a substantive text change clears them.")
        ctx.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                         error={"type": type(exc).__name__, "message": str(exc)},
                         response_code=code, context=ctx.context)
        return _ok(data, hint)
    except LexicalQueryError as exc:
        # Invalid lexical input (type/blank/over-limit/k): structured, correctable.
        code = "invalid_query"
        data = {"response_code": code, "detail": str(exc)}
        hint = "Fix the query or k and retry."
        ctx.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                         error={"type": type(exc).__name__, "message": str(exc)},
                         response_code=code, context=ctx.context)
        return _ok(data, hint)
    except InvalidReport as exc:
        data = {"recorded": False, "count": 0, "response_code": "invalid_report",
                "response_meta": {"reason": exc.reason,
                                  **({"invalid_ids": exc.invalid_ids} if exc.invalid_ids else {})}}
        hint = ("Use a search_id from a recent nonempty search in this server process "
                "and rate only distinct IDs returned by it, with integer ratings from -10 to 10. "
                "Declare feedback_source and feedback_purpose if known.")
        ctx.audit.record(name, arguments, data, (time.perf_counter() - start) * 1000,
                         error={"type": "InvalidReport", "message": str(exc)},
                         response_code="invalid_report", context=ctx.context)
        return _ok(data, hint)
    except Exception as e:
        ctx.audit.record(name, arguments, None, (time.perf_counter() - start) * 1000,
                         error={"type": type(e).__name__, "message": str(e)},
                         context=ctx.context)
        logger.error("Tool call '%s' failed: %s", name, e, exc_info=True)
        raise


# Guidance appended to each tool's result so the LLM knows what it can do next
# with the returned data. Kept intentionally concise.
_TOOL_HINTS: dict[str, str] = {
    "list_keywords": "Reuse a candidate only if it expresses your concept; new keywords are allowed. Counts reflect live memories in the requested namespace. Pass keyword_query_id to a later add/update/search when it informed your choice. No report is needed.",
    "add": "Memory stored. For future writes, use list_keywords to reuse fitting content concepts, 1–3 relevant metadata.categories from stats, and metadata.anchors for distinctive identifiers. Call get_details with memory_ids=[id] if you need to verify the stored record.",
    "search_semantic": "Results are ranked by memory-text cosine similarity. Use get_details(memory_ids=[...], source_search_id=this search_id) for full text; after using results, call report with this search_id and helpfulness ratings (-10..10).",
    "search_lexical": "Results are ranked by BM25; scores are not comparable with semantic scores. Use get_details(memory_ids=[...], source_search_id=this search_id) for full text; after using results, call report with this search_id and helpfulness ratings (-10..10).",
    "search_anchor": "Results match declared anchors and are ranked by BM25; scores are not comparable with other search scores. Use get_details(memory_ids=[...], source_search_id=this search_id) for full text; after using results, call report with this search_id and helpfulness ratings (-10..10).",
    "search_meta": "Results match exact filters, sorted by the requested time field. Use get_details(memory_ids=[...], source_search_id=this search_id) for full text; after using results, call report with this search_id and helpfulness ratings (-10..10). For conceptual lookup, use search_semantic.",
    "get_details": "Read the returned full text and metadata. To find related records, use search_meta for known exact keywords/categories, search_anchor for a declared identifier, or search_semantic for conceptual similarity.",
    "update": "Memory updated. Call get_details with memory_ids=[memory_id] if you need to verify the stored record. When changing text in future, provide an updated summary, 1–3 relevant metadata.categories, and fresh metadata.anchors when distinctive identifiers exist.",
    "delete": "Memory soft-deleted and removed from search results. Call stats if you need to verify the record count.",
    "stats": "Use namespace_counts to choose a namespace. Use search_meta for recent records or exact filters, search_semantic for concepts, search_lexical for literal text, and search_anchor for declared identifiers. Use list_keywords for the full keyword vocabulary, category_counts for category choices, and server_time for relative date ranges.",
    "report": "Feedback recorded. Continue with the task or search again if the results were unhelpful.",
}


def _result_hint(name: str, arguments: dict, data: dict) -> str:
    """Choose next-step guidance from the actual result, not only the tool name."""
    hint = _TOOL_HINTS[name]
    if name == "list_keywords":
        if data.get("mode") == "semantic":
            return (hint + " Scores measure similarity, not equivalence. Lower min_score "
                    "to broaden semantic candidates when needed.")
        return (hint + " Use offset and limit to page through browse/literal results; "
                "use semantic mode to find related terms by meaning.")
    if name == "add":
        if data.get("created") is False:
            return ("No new memory was stored: identical text already exists in this namespace. "
                    "Call get_details with memory_ids=[id] to inspect it; use update if it needs changes.")
    elif name in ("search_semantic", "search_lexical", "search_anchor", "search_meta"):
        if not data.get("items"):
            if name == "search_semantic":
                if data.get("filter_resolution") is not None:
                    return ("No memories matched. Inspect filter_resolution; adjust or remove "
                            "unresolved filters if present, otherwise broaden the query or filters. "
                            "Use stats to verify the namespace.")
                return ("No memories matched. Verify the namespace with stats; try a broader "
                        "semantic query, search_lexical for literal identifiers, or search_meta for recent records.")
            if name == "search_lexical":
                return ("No literal content match. Check the namespace with stats; try "
                        "search_anchor for a declared identifier or search_semantic for concepts/Chinese text.")
            if name == "search_anchor":
                return ("No declared anchor matched. Check the namespace with stats; try "
                        "search_lexical for content terms or search_semantic for concepts.")
            return ("No memories in this page. Check the namespace and exact filters or widen "
                    "the time range; reset offset if paginating. Try search_semantic for concepts.")
        if name == "search_semantic" and data.get("filter_resolution") is not None:
            hint += " Inspect filter_resolution to see which exact or semantic filter values were applied."
        if name == "search_meta":
            limit = int(arguments.get("limit", 100))
            if limit > 0 and len(data["items"]) == limit:
                hint += (f" This page is full; more records may exist. To continue, "
                         f"retry with offset={int(arguments.get('offset', 0)) + limit}.")
    elif name == "get_details":
        if not data.get("found"):
            return ("None of the requested IDs were found. Recheck the IDs or run a new search; "
                    "missing lists the unresolved IDs.")
        if data.get("missing"):
            hint += " Some IDs were missing; recheck those IDs or search again."
    elif name == "update":
        if not data.get("success"):
            return ("No memory was updated because the ID was not found. "
                    "Search again and use an existing ID before retrying.")
    elif name == "delete":
        if not data.get("success"):
            return ("No memory was deleted because the ID was not found or already deleted. "
                    "Search again if you need to locate it.")
    elif name == "report" and not data.get("count"):
        return ("No valid ratings were recorded. If you used a nonempty search result, "
                "pass ratings for IDs from that result and its search_id.")
    if name in ("add", "update") and data.get("categories_dropped"):
        hint += (f" Only the first {MAX_CATEGORIES} categories were kept; "
                 f"pass at most {MAX_CATEGORIES} in metadata.categories next time.")
    return hint


def _ok(data, hint: str = "") -> types.CallToolResult:
    """Serialize a tool result; attach a 'hint' field guiding the LLM on next steps.

    The hint is applied at the MCP tool boundary only — admin HTTP APIs are
    unaffected.
    """
    if isinstance(data, dict) and hint:
        data = {**data, "hint": hint}
    return types.CallToolResult(content=[
        types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, default=str)),
    ])


def _new_search_id() -> str:
    """Generate a per-call search id for correlating searches with reports."""
    return "se_" + secrets.token_hex(16)


def _wrap_search_meta(items: list, namespace: str | None, limit: int, offset: int, sort_by: str) -> dict:
    """Attach usage guidance to search_meta results so the LLM knows how to consume them.

    The raw item list must stay an array for the admin UI (/api/memories);
    this wrapper is only applied at the MCP tool boundary.
    """
    scope = f"namespace={namespace!r}" if namespace else "all namespaces"
    if items:
        note = (
            f"{len(items)} memories in this page (limit={limit}, offset={offset}) from {scope}, "
            f"sorted by {sort_by} descending. Items carry summaries. For full text, call "
            f"get_details with memory_ids and source_search_id=this search_id. After using "
            f"these results, call report with this search_id and rate reviewed ids (-10..10)."
        )
    else:
        note = f"0 memories in this page (limit={limit}, offset={offset}) from {scope}; no ids to inspect or report."
    return {"search_id": _new_search_id(), "note": note, "total": len(items), "items": items}


def _wrap_search_semantic(search_result: dict) -> dict:
    """Attach usage guidance to search_semantic results (same rationale as _wrap_search_meta)."""
    # Accept a bare list for compatibility with small integrations/tests that
    # use this response helper directly.
    if isinstance(search_result, list):
        search_result = {"items": search_result, "filter_resolution": None}
    items = search_result["items"]
    if items:
        note = (
            f"{len(items)} results ranked by semantic similarity. Items carry summaries. "
            f"For full text, call get_details with memory_ids and source_search_id=this search_id. "
            f"After using these results, call report with this search_id and rate reviewed ids (-10..10)."
        )
    else:
        note = "0 semantic results; no ids to inspect or report."
    result = {"search_id": _new_search_id(), "note": note,
              "total": len(items), "items": items}
    if search_result.get("filter_resolution") is not None:
        result["filter_resolution"] = search_result["filter_resolution"]
    return result


def _wrap_search_terms(items: list, kind: str, compared_with: str) -> dict:
    """Shared mechanical envelope for the BM25 term searches (lexical / anchor)."""
    if items:
        note = (
            f"{len(items)} results ranked by {kind} BM25. Scores are not comparable with {compared_with}. "
            f"For full text, call get_details with memory_ids and source_search_id=this search_id. "
            f"After using these results, call report with this search_id and rate reviewed ids (-10..10)."
        )
    else:
        note = f"0 {kind} results; no ids to inspect or report."
    return {"search_id": _new_search_id(), "note": note, "total": len(items), "items": items}


def _wrap_search_lexical(items: list) -> dict:
    """Attach usage guidance to search_lexical results (same rationale as search_meta)."""
    return _wrap_search_terms(items, "lexical", "semantic scores")


def _wrap_search_anchor(items: list) -> dict:
    """Attach usage guidance to search_anchor results (same rationale as search_meta)."""
    return _wrap_search_terms(items, "anchor", "other search scores")


def _namespace_error_result(exc: NamespaceNotFound, search: bool = False) -> tuple[dict, str]:
    """Build the unified unknown-namespace payload and hint.

    Used by search_semantic/search_meta (empty-result envelope) and by
    add/update (write rejected; nothing stored). Kept in one place so both
    search tools report the same shape.
    """
    available = ", ".join(repr(ns) for ns in exc.known) or "(none)"
    meta = {"requested_namespace": exc.requested, "known_namespaces": exc.known}
    if search:
        note = (f"Namespace {exc.requested!r} does not exist; 0 results returned. "
                f"Available namespaces: {available}. Retry with one of these, or omit "
                f"the namespace to search all namespaces.")
        data = {"search_id": _new_search_id(), "note": note, "total": 0, "items": [],
                "response_code": NAMESPACE_NOT_FOUND, "response_meta": meta}
        hint = (f"Namespace {exc.requested!r} does not exist, so no search ran. "
                f"Available namespaces: {available}. Retry with one of them, or omit "
                f"namespace to search all namespaces. Call 'stats' to list namespaces.")
    else:
        data = {"response_code": NAMESPACE_NOT_FOUND, "response_meta": meta}
        hint = (f"Namespace {exc.requested!r} does not exist; nothing was written. "
                f"Available namespaces: {available}. To create it deliberately, retry "
                f"with confirm_codes=[\"{NAMESPACE_NOT_FOUND}\"].")
    return data, hint


def _wrap_stats(data: dict) -> dict:
    """Strip internal details from stats — LLM only needs counts + time.

    The admin HTTP API keeps the full payload (primary/secondary/backend/…);
    this wrapper is only applied at the MCP tool boundary.
    """
    out = {k: data[k] for k in ("total", "namespace_counts", "keyword_total", "server_time")}
    out["category_counts"] = data.get("category_counts", {})
    for key in ("embed_provider", "embed_model", "embed_dimension",
                "embed_profile", "embed_dtype"):
        if key in data:
            out[key] = data[key]
    return out
