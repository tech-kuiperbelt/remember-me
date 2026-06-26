import os
import json
import sys
import asyncio
import argparse

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

from .embed import embed as embed_fn
from .storage import create_storage

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Memory MCP Server")
parser.add_argument("--port", type=int, default=None, help="Port for SSE mode")
args = parser.parse_args()

STORAGE_BACKEND = os.environ.get("MEMORY_STORAGE", "sqlite")
STORAGE_PATH = os.environ.get("MEMORY_STORAGE_PATH")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
DEFAULT_NAMESPACE = os.environ.get("MEMORY_DEFAULT_NAMESPACE", "default")

if STORAGE_BACKEND == "json":
    path = STORAGE_PATH or "data/memories.json"
    storage = create_storage("json", path=path)
else:
    path = STORAGE_PATH or "data/memories.db"
    storage = create_storage("sqlite", path=path)

server = Server("memory-server")


def _embed(text: str) -> list[float]:
    return embed_fn(text, base_url=OLLAMA_BASE_URL, model=EMBED_MODEL)


def _strip_embedding(d: dict) -> dict:
    d.pop("embedding", None)
    return d


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
            description="Update a memory's text and/or metadata. If text changes, embedding is recomputed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "text": {"type": "string", "description": "New text"},
                    "metadata": {"type": "object", "description": "Metadata to merge"},
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
    # -- add --
    if name == "add":
        text = arguments["text"]
        eid = storage.add(
            text,
            _embed(text),
            metadata=arguments.get("metadata"),
            namespace=arguments.get("namespace", DEFAULT_NAMESPACE),
        )
        return _ok({"id": eid})

    # -- search --
    if name == "search":
        query_emb = _embed(arguments["query"])
        results = storage.search(
            query_emb,
            k=int(arguments.get("k", 5)),
            threshold=float(arguments.get("threshold", 0.0)),
            namespace=arguments.get("namespace"),
        )
        return _ok([_strip_embedding(r) for r in results])

    # -- list --
    if name == "list":
        results = storage.list_memories(
            namespace=arguments.get("namespace"),
            limit=int(arguments.get("limit", 100)),
            offset=int(arguments.get("offset", 0)),
        )
        return _ok([_strip_embedding(r) for r in results])

    # -- get --
    if name == "get":
        result = storage.get(arguments["memory_id"])
        return _ok(_strip_embedding(result) if result else None)

    # -- update --
    if name == "update":
        mid = arguments["memory_id"]
        text = arguments.get("text")
        embedding = _embed(text) if text else None
        ok = storage.update(mid, text=text, embedding=embedding, metadata=arguments.get("metadata"))
        return _ok({"success": ok})

    # -- delete --
    if name == "delete":
        ok = storage.delete(arguments["memory_id"])
        return _ok({"success": ok})

    # -- clear --
    if name == "clear":
        n = storage.clear(namespace=arguments.get("namespace"))
        return _ok({"deleted": n})

    # -- stats --
    if name == "stats":
        stats = storage.get_stats()
        stats["embed_model"] = EMBED_MODEL
        stats["ollama_base_url"] = OLLAMA_BASE_URL
        return _ok(stats)

    # -- import --
    if name == "import":
        src_path = arguments["file_path"]
        src_backend = "json" if src_path.endswith(".json") else "sqlite"
        src = create_storage(src_backend, path=src_path)
        result = storage.merge(src, namespace_prefix=arguments.get("namespace_prefix", ""))
        return _ok(result)

    raise ValueError(f"Unknown tool: {name}")


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
    if uri == "memories://stats":
        return types.ReadResourceResult(contents=[
            types.TextResourceContents(uri=uri, mimeType="application/json",
                                       text=json.dumps(storage.get_stats(), ensure_ascii=False)),
        ])
    if uri == "memories://list":
        results = storage.list_memories(limit=1000)
        return types.ReadResourceResult(contents=[
            types.TextResourceContents(uri=uri, mimeType="application/json",
                                       text=json.dumps([_strip_embedding(r) for r in results],
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
