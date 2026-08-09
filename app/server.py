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
            description="Store a new memory. Text is automatically embedded for semantic search.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The memory text"},
                    "metadata": {"type": "object", "description": "Optional key-value metadata"},
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
                        "If no results are found, call 'namespaces' first to verify the correct namespace, "
                        "then try searching again.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text — automatically embedded for semantic similarity matching"},
                    "k": {"type": "integer", "description": "Max number of results to return (default: 5). Increase this (e.g. 20) when searching broadly or if results seem sparse."},
                    "threshold": {"type": "number", "description": "Minimum similarity score 0-1 (default: 0 = no filter). Higher values return fewer but more relevant results. Keep at 0 unless you want to narrow down."},
                    "namespace": {"type": "string", "description": "Only search within this namespace (optional). Leave empty to search all namespaces. If you get no results, call 'namespaces' first to check available namespaces."},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="list",
            description="List memories ordered by time, with pagination and optional filters "
                        "(namespace / updated_at time range). "
                        "Results return the memory summary (falling back to original text when no summary exists); "
                        "call 'get_details' with the returned IDs to fetch full content. "
                        "Use this for time-based questions such as \"what did we do recently / last session\" "
                        "— combine sort_by='updated_at' with a small limit to get the most recently "
                        "written or changed memories. For topic/keyword questions use 'search' instead. "
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
                },
            },
        ),
        types.Tool(
            name="get",
            description="Get a memory by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                },
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="get_details",
            description="Fetch full content for multiple memories by ID. "
                        "Returns the complete text of each found memory plus the list of IDs that "
                        "do not exist (partial success). Use this when a search/list summary looks "
                        "relevant and you need the full original content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of memory IDs to fetch full content for",
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
            name="clear",
            description="Delete all memories (or within a namespace). Use with caution — there is no undo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="stats",
            description="Storage statistics and configuration. Includes 'server_time' (current UTC time) — "
                        "check this first when a user asks a time-based question (e.g. \"what did we do last week / this month\"), "
                        "so you can compute the correct updated_after/updated_before values for the 'list' tool.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="import",
            description="Import memories from another storage file. Supports .db, .jsonl and .json.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to source DB/JSON file"},
                    "namespace_prefix": {"type": "string", "description": "Optional prefix for imported namespaces"},
                },
                "required": ["file_path"],
            },
        ),
        types.Tool(
            name="sync",
            description="Force full rebuild of the secondary/backup storage from the primary database. Safe to call anytime — no data is lost, old backup data is replaced. Only available in dual storage mode.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="recover",
            description="Rebuild target storage from source storage file. Clears target first. For disaster recovery.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "Path to source storage file"},
                    "target_path": {"type": "string", "description": "Path to target storage file (will be overwritten)"},
                },
                "required": ["source_path", "target_path"],
            },
        ),
        types.Tool(
            name="summarize",
            description="Generate summaries for existing memories. "
                        "mode='missing' (default) only processes memories whose summary is empty; "
                        "mode='all' regenerates every summary (use after a summary model/algorithm change). "
                        "Pass memory_id to process a single memory by ID. "
                        "Summary-only updates do not change updated_at.",
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Optional namespace filter"},
                    "mode": {"type": "string", "enum": ["missing", "all"],
                             "description": "'missing' (default) = only empty summaries; 'all' = regenerate every summary"},
                    "memory_id": {"type": "string",
                                  "description": "Optional: only generate a summary for this memory ID"},
                },
            },
        ),
        types.Tool(
            name="namespaces",
            description="List all existing namespace names and their record counts. "
                        "Use this for admin audit — check available namespaces before "
                        "search or add to avoid missing results or creating duplicates. "
                        "The default namespace is set via MEMORY_DEFAULT_NAMESPACE env var.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ])


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> types.CallToolResult:
    logger.info("Tool call: %s %s", name, arguments)
    try:
        if name == "add":
            return _ok(memory_add(arguments["text"], arguments.get("metadata"), arguments.get("namespace")))
        if name == "search":
            return _ok(_prepare_for_llm(memory_search(arguments["query"], int(arguments.get("k", 5)),
                                                      float(arguments.get("threshold", 0.0)), arguments.get("namespace"))))
        if name == "list":
            return _ok(_prepare_for_llm(memory_list(arguments.get("namespace"), int(arguments.get("limit", 100)),
                                                    int(arguments.get("offset", 0)), arguments.get("sort_by", "created_at"),
                                                    arguments.get("updated_after"), arguments.get("updated_before"))))
        if name == "get":
            return _ok(memory_get(arguments["memory_id"]))
        if name == "get_details":
            return _ok(memory_get_details(arguments["memory_ids"]))
        if name == "update":
            return _ok(memory_update(arguments["memory_id"], arguments.get("text"), arguments.get("metadata"), arguments.get("namespace")))
        if name == "delete":
            return _ok(memory_delete(arguments["memory_id"]))
        if name == "clear":
            return _ok(memory_clear(arguments.get("namespace")))
        if name == "stats":
            return _ok(memory_stats())
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

def _ok(data) -> types.CallToolResult:
    return types.CallToolResult(content=[
        types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, default=str)),
    ])


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
