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
    memory_update, memory_delete, memory_clear, memory_stats, memory_import

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
            description="Semantic search across memories by query text.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "k": {"type": "number", "description": "Max results (default: 5)"},
                    "threshold": {"type": "number", "description": "Min similarity 0-1 (default: 0)"},
                    "namespace": {"type": "string", "description": "Filter by namespace"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="list",
            description="List memories, optionally filtered by namespace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "limit": {"type": "number", "description": "Max results (default: 100)"},
                    "offset": {"type": "number", "description": "Pagination offset (default: 0)"},
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
            name="update",
            description="Update a memory's text and/or metadata and/or namespace. If text changes, embedding is recomputed.",
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
            description="Delete a memory by ID.",
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
            description="Delete all memories, optionally within a namespace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="stats",
            description="Storage statistics and configuration.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="import",
            description="Import memories from another database file. Supports .db and .json.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to source DB/JSON file"},
                    "namespace_prefix": {"type": "string", "description": "Optional prefix for imported namespaces"},
                },
                "required": ["file_path"],
            },
        ),
    ])


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> types.CallToolResult:
    logger.info("Tool call: %s %s", name, arguments)
    try:
        if name == "add":
            return _ok(memory_add(arguments["text"], arguments.get("metadata"), arguments.get("namespace")))
        if name == "search":
            return _ok(memory_search(arguments["query"], int(arguments.get("k", 5)),
                                     float(arguments.get("threshold", 0.0)), arguments.get("namespace")))
        if name == "list":
            return _ok(memory_list(arguments.get("namespace"), int(arguments.get("limit", 100)),
                                   int(arguments.get("offset", 0))))
        if name == "get":
            return _ok(memory_get(arguments["memory_id"]))
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
                                       text=json.dumps(memory_list(limit=1000),
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
        await uv_server.serve()
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
