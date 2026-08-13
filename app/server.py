import json
import sys
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
from .service import memory_add, memory_search, memory_list, memory_get, \
    memory_update, memory_delete, memory_clear, memory_stats, memory_import, \
    memory_sync, memory_recover, memory_namespaces, memory_get_details, \
    memory_summarize, _prepare_for_llm

logger = logging.getLogger("server")

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
                        "Deduplicated by content+namespace: if the same text already exists in the "
                        "target namespace, no duplicate is inserted and the existing record's id is "
                        "returned with created=false. "
                        "Before adding, call 'stats' to inspect keyword_counts and reuse an existing "
                        "keyword when semantically appropriate; create a new keyword only when no "
                        "existing keyword matches. Pass keywords as an array in metadata.keywords — "
                        "they are content concepts (e.g. entities, topics), NOT category/type.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The memory text"},
                    "metadata": {
                        "type": "object",
                        "description": "Optional key-value metadata",
                        "properties": {
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Content keywords (concrete concepts in the memory, not category/type). Normalized to lowercase.",
                            },
                        },
                    },
                    "namespace": {"type": "string", "description": "Namespace (default: $MEMORY_DEFAULT_NAMESPACE or 'default')"},
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="search",
            description="Semantic search across memories by query text. "
                        "Use this for topic/keyword questions — e.g. \"what do we know about X\", "
                        "\"anything related to Y\" — where you want the most semantically similar memories. "
                        "Results return the memory summary (falling back to original text when no summary exists); "
                        "call 'get_details' with the returned IDs to fetch full content. "
                        "For time-based questions (e.g. \"what did we do recently / last session\") "
                        "use 'list' with sort_by='updated_at' instead. "
                        "If no results are found, call 'stats' first to verify the correct namespace, "
                        "then try searching again.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text — automatically embedded for semantic similarity matching"},
                    "k": {"type": "integer", "description": "Max number of results to return (default: 5). Increase this (e.g. 20) when searching broadly or if results seem sparse."},
                    "threshold": {"type": "number", "description": "Minimum similarity score 0-1 (default: 0 = no filter). Higher values return fewer but more relevant results. Keep at 0 unless you want to narrow down."},
                    "namespace": {"type": "string", "description": "Only search within this namespace (optional). Leave empty to search all namespaces. If you get no results, call 'stats' first to check available namespaces."},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="list",
            description="List memories ordered by time, with pagination and optional filters "
                        "(namespace / updated_at time range / keywords). "
                        "Results return the memory summary (falling back to original text when no summary exists); "
                        "call 'get_details' with the returned IDs to fetch full content. "
                        "Use this for time-based questions such as \"what did we do recently / last session\" "
                        "— combine sort_by='updated_at' with a small limit to get the most recently "
                        "written or changed memories. For topic/keyword questions use 'search' instead. "
                        "For keyword filtering, call 'stats' first to see available keywords in "
                        "keyword_counts. Multiple keywords match ANY (OR). Keywords combine with "
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
                },
            },
        ),
        types.Tool(
            name="get_details",
            description="Fetch full content for one or more memories by ID. "
                        "Accepts a single ID or an array of IDs. "
                        "Returns the complete text of each found memory plus the list of IDs that "
                        "do not exist (partial success). Use this when a search/list summary looks "
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
            description="Update a memory's text and/or metadata and/or namespace. "
                        "If text changes, embedding and summary are recomputed; "
                        "metadata-only updates leave the summary untouched.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "text": {"type": "string", "description": "New text"},
                    "metadata": {"type": "object", "description": "Metadata to merge"},
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
                        "and 'server_time' (current UTC time). Check 'server_time' first when a user asks "
                        "a time-based question (e.g. \"what did we do last week / this month\") so you can "
                        "compute the correct updated_after/updated_before values for the 'list' tool. The "
                        "default namespace is set via MEMORY_DEFAULT_NAMESPACE env var.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ])


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> types.CallToolResult:
    logger.info("Tool call: %s %s", name, arguments)
    try:
        if name == "add":
            return _ok(memory_add(arguments["text"], arguments.get("metadata"), arguments.get("namespace")),
                       _TOOL_HINTS["add"])
        if name == "search":
            return _ok(_wrap_search(_prepare_for_llm(memory_search(arguments["query"], int(arguments.get("k", 5)),
                                                                    float(arguments.get("threshold", 0.0)), arguments.get("namespace")))),
                       _TOOL_HINTS["search"])
        if name == "list":
            ns = arguments.get("namespace")
            limit = int(arguments.get("limit", 100))
            offset = int(arguments.get("offset", 0))
            sort_by = arguments.get("sort_by", "created_at")
            items = _prepare_for_llm(memory_list(ns, limit, offset, sort_by,
                                                 arguments.get("updated_after"), arguments.get("updated_before"),
                                                 keywords=arguments.get("keywords")))
            return _ok(_wrap_list(items, ns, limit, offset, sort_by), _TOOL_HINTS["list"])
        if name == "get":
            return _ok(memory_get(arguments["memory_id"]))
        if name == "get_details":
            return _ok(memory_get_details(arguments["memory_ids"]), _TOOL_HINTS["get_details"])
        if name == "update":
            return _ok(memory_update(arguments["memory_id"], arguments.get("text"), arguments.get("metadata"), arguments.get("namespace")),
                       _TOOL_HINTS["update"])
        if name == "delete":
            return _ok(memory_delete(arguments["memory_id"]), _TOOL_HINTS["delete"])
        if name == "clear":
            return _ok(memory_clear(arguments.get("namespace")))
        if name == "stats":
            return _ok(_wrap_stats(memory_stats()), _TOOL_HINTS["stats"])
        if name == "import":
            return _ok(memory_import(arguments["file_path"], arguments.get("namespace_prefix", "")))
        if name == "sync":
            return _ok(memory_sync())
        if name == "recover":
            return _ok(memory_recover(arguments["source_path"], arguments["target_path"]))
        if name == "summarize":
            return _ok(memory_summarize(arguments.get("namespace"), arguments.get("mode", "missing"),
                                        arguments.get("memory_id")))
        if name == "namespaces":
            return _ok(memory_namespaces())
        raise ValueError(f"Unknown tool: {name}")
    except Exception as e:
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
    "add": "Memory stored (created=true). If created=false, an identical record already exists in this namespace and the returned id is the existing one — consider 'update' instead of re-adding.",
    "search": "Results are ranked by relevance (score). Use 'get_details' with an id to read the full original text.",
    "list": "Use 'get_details' with an id to read the full original text, or call 'search' for topic/keyword lookup.",
    "get_details": "You now have the full text, including metadata.keywords. If a memory is relevant, use list(keywords=[...]) with its keywords to find related memories.",
    "update": "Memory updated. Use 'get_details' with the id to verify the change, or 'search' to find related memories.",
    "delete": "Memory soft-deleted. It no longer appears in 'search'/'list'. Call 'stats' to confirm the count.",
    "stats": "Use 'namespace_counts' to pick a namespace, then 'list' or 'search' within it. Use 'keyword_counts' as the canonical keyword vocabulary for list filtering and add. Use 'server_time' for time-based queries.",
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


def _wrap_list(items: list, namespace: str | None, limit: int, offset: int, sort_by: str) -> dict:
    """Attach usage guidance to list results so the LLM knows how to consume them.

    The raw item list must stay an array for the admin UI (/api/memories);
    this wrapper is only applied at the MCP tool boundary.
    """
    scope = f"namespace={namespace!r}" if namespace else "all namespaces"
    note = (
        f"{len(items)} memories (limit={limit}, offset={offset}) from {scope}, "
        f"sorted by {sort_by} descending. Items carry summary (full text when no "
        f"summary yet). To see the complete original content, call 'get_details' "
        f"with the returned ids."
    )
    return {"note": note, "total": len(items), "items": items}


def _wrap_search(items: list) -> dict:
    """Attach usage guidance to search results (same rationale as _wrap_list)."""
    note = (
        f"{len(items)} results, ranked by semantic similarity (score). Items carry "
        f"summary (full text when no summary yet). To see the complete original "
        f"content, call 'get_details' with the returned ids."
    )
    return {"note": note, "total": len(items), "items": items}


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
