import json
import sys
import time
import secrets
import asyncio
import argparse
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.types import ServerCapabilities, ToolsCapability, ResourcesCapability
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response
import uvicorn

from . import log_config  # noqa: F401
from .service import memory_add, memory_search, memory_list, \
    memory_update, memory_delete, memory_stats, memory_get_details, \
    memory_report, _prepare_for_llm
from .audit import record as audit_record
from .categories import CATEGORY_VOCABULARY, MAX_CATEGORIES

logger = logging.getLogger("server")

_CATEGORIES_JOINED = ", ".join(CATEGORY_VOCABULARY)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Memory MCP Server")
parser.add_argument("--port", type=int, default=None, help="Port for SSE mode")
args = parser.parse_args()

server = Server("memory-server")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@server.list_tools()
async def handle_list_tools() -> types.ListToolsResult:
    return types.ListToolsResult(tools=[
        types.Tool(
            name="add",
            description="Store a new memory. Text is automatically embedded for semantic search. "
                        "You MUST provide a concise, faithful summary in the summary parameter; "
                        "keep technical terms, ticket numbers, and business event times. The server "
                        "still accepts a missing or blank summary and generates one asynchronously "
                        "as a reliability fallback. "
                        "Deduplicated by content+namespace: if the same text already exists in the "
                        "target namespace, no duplicate is inserted and the existing record's id is "
                        "returned with created=false. "
                        "Before adding, call 'stats' to inspect keyword_counts and category_counts "
                        "and reuse an existing keyword/category when semantically appropriate; "
                        "create a new one only when no existing value matches. Pass keywords as an "
                        "array in metadata.keywords — they are content concepts (e.g. entities, "
                        "topics). Pass categories as an array in metadata.categories — memory "
                        "nature/classification, up to " + str(MAX_CATEGORIES) + " values, prefer "
                        "these canonical categories: " + _CATEGORIES_JOINED + ".",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The memory text"},
                    "summary": {
                        "type": "string",
                        "description": "A concise summary of text. LLM callers MUST provide this; missing or blank values are tolerated and generated asynchronously as fallback.",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional key-value metadata",
                        "properties": {
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Content keywords (concrete concepts in the memory, not category/type). Prefer reusing existing keywords from stats' keyword_counts; create a new keyword only when none fits. Normalized to lowercase.",
                            },
                            "categories": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": MAX_CATEGORIES,
                                "description": "Memory nature/classification, 1-%d values. Prefer the canonical categories, only introduce a new one when none fits: %s. Normalized to lowercase." % (MAX_CATEGORIES, _CATEGORIES_JOINED),
                            },
                        },
                    },
                    "namespace": {"type": "string", "description": "Namespace (default: $MEMORY_DEFAULT_NAMESPACE or 'default')"},
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="search_semantic",
            description="Semantic search across memories by query text. "
                        "Use this for topic/keyword questions — e.g. \"what do we know about X\", "
                        "\"anything related to Y\" — where you want the most semantically similar memories. "
                        "Results return the memory summary (falling back to original text when no summary exists); "
                        "call 'get_details' with the returned IDs to fetch full content. "
                        "For time-based questions (e.g. \"what did we do recently / last session\") "
                        "use 'search_meta' with sort_by='updated_at' instead. "
                        "You may optionally pass keywords= and/or categories= to pre-filter candidates "
                        "before similarity scoring. Values within each dimension use OR; keywords and "
                        "categories combine with each other and namespace using AND. Call 'stats' first "
                        "to see keyword_counts and category_counts. "
                        "If no results are found, call 'stats' first to verify the correct namespace, "
                        "then try searching again.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text — automatically embedded for semantic similarity matching"},
                    "k": {"type": "integer", "description": "Max number of results to return (default: 5). Increase this (e.g. 20) when searching broadly or if results seem sparse."},
                    "threshold": {"type": "number", "description": "Minimum similarity score 0-1 (default: 0 = no filter). Higher values return fewer but more relevant results. Keep at 0 unless you want to narrow down."},
                    "namespace": {"type": "string", "description": "Only search within this namespace (optional). Leave empty to search all namespaces. If you get no results, call 'stats' first to check available namespaces."},
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict the search to memories carrying any of these keywords (OR). Combined with categories and namespace using AND. Call 'stats' first to see keyword_counts.",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict the search to memories carrying any of these categories (OR). Combined with keywords and namespace using AND. Call 'stats' first to see category_counts.",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="search_meta",
            description="List memories by exact criteria: keywords, categories, namespace, "
                        "updated_at time range, "
                        "sort order and pagination. "
                        "Results return the memory summary (falling back to original text when no summary exists); "
                        "call 'get_details' with the returned IDs to fetch full content. "
                        "Use this for time-based questions such as \"what did we do recently / last session\" "
                        "— combine sort_by='updated_at' with a small limit to get the most recently "
                        "written or changed memories. For fuzzy/topic/keyword questions use 'search_semantic' instead. "
                        "For keyword/category filtering, call 'stats' first to see available keywords in "
                        "keyword_counts and categories in category_counts. Multiple keywords match ANY (OR), "
                        "and multiple categories match ANY (OR). Keywords/categories combine with "
                        "namespace and time filters using AND. "
                        "For relative time windows (\"last week\", \"this month\"), call 'stats' first to get "
                        "server_time, then pass concrete updated_after/updated_before timestamps. "
                        "Ordered by the given field descending (default: created_at).",
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Only list memories in this namespace (optional). Leave empty to list all namespaces."},
                    "limit": {"type": "number", "description": "Max results (default: 100). Use a small value (e.g. 5-10) for recent-memory lookups."},
                    "offset": {"type": "number", "description": "Pagination offset (default: 0)"},
                    "sort_by": {"type": "string", "enum": ["created_at", "updated_at"],
                                "description": "Sort field, descending. Default: created_at. Use 'updated_at' for most recently written/changed first."},
                    "updated_after": {"type": "string", "description": "ISO8601 timestamp (e.g. '2026-07-01T00:00:00+00:00'). Only return memories updated after this moment (exclusive). If the user asks for a relative window (e.g. 'last week', 'this month') and you are unsure of the current date, call 'stats' first to get its 'server_time', then compute this value."},
                    "updated_before": {"type": "string", "description": "ISO8601 timestamp (e.g. '2026-07-01T00:00:00+00:00'). Only return memories updated before this moment (exclusive). If the user asks for a relative window (e.g. 'last week', 'this month') and you are unsure of the current date, call 'stats' first to get its 'server_time', then compute this value."},
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by global keywords — any match is sufficient (OR). Call 'stats' first to see available keywords in keyword_counts.",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by memory categories — any match is sufficient (OR). Call 'stats' first to see canonical categories in category_counts.",
                    },
                },
            },
        ),
        types.Tool(
            name="get_details",
            description="Fetch full content for one or more memories by ID. "
                        "Accepts a single ID or an array of IDs. "
                        "Returns the complete text of each found memory plus the list of IDs that "
                        "do not exist (partial success). Use this when a search_semantic/search_meta summary looks "
                        "relevant and you need the full original content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of memory IDs to fetch full content for (a single ID also works)",
                    },
                },
                "required": ["memory_ids"],
            },
        ),
        types.Tool(
            name="update",
            description="Update a memory's text, summary, metadata and/or namespace. "
                        "When changing text, you MUST also provide a concise, faithful summary for "
                        "the new text. The server still accepts an omitted or blank summary and "
                        "generates one asynchronously as a reliability fallback. A summary may also "
                        "be corrected without changing text; metadata-only updates leave it untouched. "
                        "When updating metadata you may pass metadata.keywords — content concepts; "
                        "reuse existing keywords from stats' keyword_counts when one fits; an empty "
                        "array clears. You may also pass metadata.categories — memory "
                        "nature/classification, up to " + str(MAX_CATEGORIES) + " values, prefer the "
                        "canonical categories from stats' category_counts: " + _CATEGORIES_JOINED + ". "
                        "An empty array clears the memory's categories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "text": {"type": "string", "description": "New text"},
                    "summary": {"type": "string", "description": "New summary. LLM callers changing text MUST provide this; omission is tolerated and triggers asynchronous fallback."},
                    "metadata": {
                        "type": "object",
                        "description": "Metadata to merge",
                        "properties": {
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Content keywords (concrete concepts in the memory, not category/type). Prefer reusing existing keywords from stats' keyword_counts; create a new keyword only when none fits. Normalized to lowercase. Empty array clears.",
                            },
                            "categories": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": MAX_CATEGORIES,
                                "description": "Memory nature/classification, 1-%d values. Prefer the canonical categories, only introduce a new one when none fits: %s. Normalized to lowercase. Empty array clears." % (MAX_CATEGORIES, _CATEGORIES_JOINED),
                            },
                        },
                    },
                    "namespace": {"type": "string", "description": "New namespace"},
                },
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="delete",
            description="Soft-delete a memory by ID. It is removed from search results but can technically be restored via force_sync.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                },
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="stats",
            description="Storage statistics. Returns 'total' (record count), 'namespace_counts' "
                        "(map of namespace -> record count, e.g. check namespaces before search/add to "
                        "avoid missing results or creating duplicates), 'keyword_counts' (the global "
                        "canonical keyword vocabulary — reuse these when adding or filtering memories), "
                        "'category_counts' (the canonical category vocabulary — with the seed values "
                        "always listed, count 0 when unused; reuse these up to 3 values per memory), "
                        "and 'server_time' (current UTC time). Check 'server_time' first when a user asks "
                        "a time-based question (e.g. \"what did we do last week / this month\") so you can "
                        "compute the correct updated_after/updated_before values for the 'search_meta' tool. The "
                        "default namespace is set via MEMORY_DEFAULT_NAMESPACE env var.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="report",
            description="Report how helpful a previous search's results were for the current "
                        "question or context. You must call this AFTER using results from "
                        "search_semantic/search_meta — pass the search_id from that search's "
                        "response plus ratings per memory id. rating is an integer from -10 to 10: "
                        "positive = helpful (larger = more useful), negative = misleading/interference "
                        "(more negative = stronger interference), 0 = neither helpful nor harmful. "
                        "You may rate any subset of the returned ids. The feedback is recorded for "
                        "later search-quality analysis and takes one tool call — do not skip it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search_id": {"type": "string", "description": "The search_id from a previous search_semantic/search_meta response"},
                    "ratings": {
                        "type": "array",
                        "description": "Per-memory feedback. Rating -10..10 (out-of-range values are clamped).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "m_id": {"type": "string", "description": "Memory id from the search results"},
                                "rating": {"type": "integer", "description": "-10..10; positive helpful, negative interfering, 0 neutral"},
                            },
                            "required": ["m_id", "rating"],
                        },
                    },
                },
                "required": ["search_id", "ratings"],
            },
        ),
    ])


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> types.CallToolResult:
    logger.info("Tool call: %s %s", name, arguments)
    start = time.perf_counter()
    try:
        if name == "add":
            data = memory_add(arguments["text"], arguments.get("metadata"),
                              arguments.get("namespace"), arguments.get("summary"))
            hint = _TOOL_HINTS["add"]
            if data.get("categories_dropped"):
                hint += (" You provided more than %d categories; only the first %d were kept — "
                         "next time pass at most %d in metadata.categories."
                         % (MAX_CATEGORIES, MAX_CATEGORIES, MAX_CATEGORIES))
        elif name == "search_semantic":
            data = _wrap_search_semantic(_prepare_for_llm(
                memory_search(arguments["query"], int(arguments.get("k", 5)),
                              float(arguments.get("threshold", 0.0)), arguments.get("namespace"),
                              keywords=arguments.get("keywords"),
                              categories=arguments.get("categories"))))
            hint = _TOOL_HINTS["search_semantic"]
        elif name == "search_meta":
            ns = arguments.get("namespace")
            limit = int(arguments.get("limit", 100))
            offset = int(arguments.get("offset", 0))
            sort_by = arguments.get("sort_by", "created_at")
            items = _prepare_for_llm(memory_list(ns, limit, offset, sort_by,
                                                 arguments.get("updated_after"), arguments.get("updated_before"),
                                                 keywords=arguments.get("keywords"),
                                                 categories=arguments.get("categories")))
            data = _wrap_search_meta(items, ns, limit, offset, sort_by)
            hint = _TOOL_HINTS["search_meta"]
        elif name == "get_details":
            data = memory_get_details(arguments["memory_ids"])
            hint = _TOOL_HINTS["get_details"]
        elif name == "update":
            data = memory_update(arguments["memory_id"], arguments.get("text"),
                                 arguments.get("metadata"), arguments.get("namespace"),
                                 arguments.get("summary"))
            hint = _TOOL_HINTS["update"]
            if data.get("categories_dropped"):
                hint += (" You provided more than %d categories; only the first %d were kept — "
                         "next time pass at most %d in metadata.categories."
                         % (MAX_CATEGORIES, MAX_CATEGORIES, MAX_CATEGORIES))
        elif name == "delete":
            data = memory_delete(arguments["memory_id"])
            hint = _TOOL_HINTS["delete"]
        elif name == "stats":
            data = _wrap_stats(memory_stats())
            hint = _TOOL_HINTS["stats"]
        elif name == "report":
            data = memory_report(arguments.get("search_id"), arguments.get("ratings"))
            hint = _TOOL_HINTS["report"]
        else:
            raise ValueError(f"Unknown tool: {name}")
        audit_record(name, arguments, data, (time.perf_counter() - start) * 1000)
        return _ok(data, hint)
    except Exception as e:
        audit_record(name, arguments, None, (time.perf_counter() - start) * 1000,
                     error={"type": type(e).__name__, "message": str(e)})
        logger.error("Tool call '%s' failed: %s", name, e, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@server.list_resources()
async def handle_list_resources() -> types.ListResourcesResult:
    return types.ListResourcesResult(resources=[
        types.Resource(
            uri="memories://stats",
            name="Memory Stats",
            description="Storage statistics",
            mimeType="application/json",
        ),
        types.Resource(
            uri="memories://list",
            name="All Memories",
            description="All stored memories (no embeddings)",
            mimeType="application/json",
        ),
    ])


@server.read_resource()
async def handle_read_resource(uri: str) -> types.ReadResourceResult:
    logger.info("Resource read: %s", uri)
    if uri == "memories://stats":
        return types.ReadResourceResult(contents=[
            types.TextResourceContents(uri=uri, mimeType="application/json",
                                       text=json.dumps(memory_stats(), ensure_ascii=False)),
        ])
    if uri == "memories://list":
        return types.ReadResourceResult(contents=[
            types.TextResourceContents(uri=uri, mimeType="application/json",
                                       text=json.dumps(_prepare_for_llm(memory_list(limit=1000)),
                                                       ensure_ascii=False, default=str)),
        ])
    raise ValueError(f"Unknown resource: {uri}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Guidance appended to each tool's result so the LLM knows what it can do next
# with the returned data. Kept intentionally concise.
_TOOL_HINTS: dict[str, str] = {
    "add": "Memory stored. A supplied summary was saved directly; if it was missing or blank, asynchronous summary generation was queued. If created=false, an identical record already exists and remains untouched — consider 'update' instead. Reuse canonical categories from stats' category_counts (up to 3).",
    "search_semantic": "Results are ranked by relevance (score). Use 'get_details' with an id to read the full original text, then call 'report' with this search_id to rate how helpful each result was (-10..10). keywords/categories pre-filter candidates before scoring: values within one dimension use OR, while dimensions combine using AND. The 3-category cap applies only to a memory, not a filter. Use stats for canonical values; if results are sparse, widen or drop filters.",
    "search_meta": "Use 'get_details' with an id to read the full original text, or call 'search_semantic' for fuzzy/topic/keyword lookup. Filter further with keywords= and/or categories=. After using these results, call 'report' with this search_id to rate how helpful each id was (-10..10).",
    "get_details": "You now have the full text, including metadata.keywords and metadata.categories. If a memory is relevant, use search_meta(keywords=[...]) or search_meta(categories=[...]) to find related memories.",
    "update": "Memory updated. A supplied summary was saved directly; a text change without one queued asynchronous summary generation. Use 'get_details' with the id to verify the change. Categories (if any) were taken from metadata.categories — see stats' category_counts for the canonical list (up to 3).",
    "delete": "Memory soft-deleted. It no longer appears in 'search_semantic'/'search_meta'. Call 'stats' to confirm the count.",
    "stats": "Use 'namespace_counts' to pick a namespace, then 'search_meta' or 'search_semantic' within it. Use 'keyword_counts' as the canonical keyword vocabulary and 'category_counts' as the canonical category vocabulary (up to 3 per memory) for search_meta/add. Use 'server_time' for time-based queries.",
    "report": "Feedback recorded. Continue with the task, or run another search if the results were unhelpful.",
}


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
    return "se_" + secrets.token_hex(4)


def _wrap_search_meta(items: list, namespace: str | None, limit: int, offset: int, sort_by: str) -> dict:
    """Attach usage guidance to search_meta results so the LLM knows how to consume them.

    The raw item list must stay an array for the admin UI (/api/memories);
    this wrapper is only applied at the MCP tool boundary.
    """
    scope = f"namespace={namespace!r}" if namespace else "all namespaces"
    note = (
        f"{len(items)} memories (limit={limit}, offset={offset}) from {scope}, "
        f"sorted by {sort_by} descending. Items carry summary (full text when no "
        f"summary yet). To see the complete original content, call 'get_details' "
        f"with the returned ids. After using these results, call 'report' with "
        f"this search_id and rate how helpful each id was for the current question "
        f"(-10..10, may rate a subset)."
    )
    return {"search_id": _new_search_id(), "note": note, "total": len(items), "items": items}


def _wrap_search_semantic(items: list) -> dict:
    """Attach usage guidance to search_semantic results (same rationale as _wrap_search_meta)."""
    note = (
        f"{len(items)} results, ranked by semantic similarity (score). Items carry "
        f"summary (full text when no summary yet). To see the complete original "
        f"content, call 'get_details' with the returned ids. After using these "
        f"results, call 'report' with this search_id and rate how helpful each id "
        f"was for the current question (-10..10, may rate a subset)."
    )
    return {"search_id": _new_search_id(), "note": note, "total": len(items), "items": items}


def _trim_keyword_counts(keyword_counts: dict) -> dict:
    """Thin wrapper — real logic lives in app.keyword_trim."""
    from .keyword_trim import trim_keyword_counts
    return trim_keyword_counts(keyword_counts)


def _wrap_stats(data: dict) -> dict:
    """Strip internal details from stats — LLM only needs counts + time.

    The admin HTTP API keeps the full payload (primary/secondary/backend/…);
    this wrapper is only applied at the MCP tool boundary.
    """
    out = {k: data[k] for k in ("total", "namespace_counts", "keyword_counts", "server_time")}
    out["keyword_counts"] = _trim_keyword_counts(out["keyword_counts"])
    out["category_counts"] = data.get("category_counts", {})
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    if args.port is not None:
        # ---- SSE mode ----
        logger.info("Starting SSE server on port %d", args.port)
        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await server.run(
                    streams[0], streams[1],
                    InitializationOptions(
                        server_name="memory-server",
                        server_version="1.0.0",
                        capabilities=ServerCapabilities(
                            tools=ToolsCapability(listChanged=False),
                            resources=ResourcesCapability(subscribe=False),
                        ),
                    ),
                )
            return Response()

        from .admin import admin_routes

        routes = [
            Route("/sse", endpoint=handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse.handle_post_message),
        ] + admin_routes
        starlette_app = Starlette(routes=routes)
        config = uvicorn.Config(starlette_app, host="0.0.0.0", port=args.port, log_level="info")
        uv_server = uvicorn.Server(config)
        sys.stderr.write(f"SSE server listening on http://0.0.0.0:{args.port}/sse\n")
        try:
            await uv_server.serve()
        finally:
            from . import summary_worker
            summary_worker.shutdown()
    else:
        # ---- stdio mode ----
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="memory-server",
                    server_version="1.0.0",
                    capabilities=ServerCapabilities(
                        tools=ToolsCapability(listChanged=False),
                        resources=ResourcesCapability(subscribe=False),
                    ),
                ),
            )


if __name__ == "__main__":
    asyncio.run(main())
