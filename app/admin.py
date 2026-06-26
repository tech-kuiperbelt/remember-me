import json
import sys
from pathlib import Path
from starlette.responses import Response, FileResponse
from starlette.routing import Route
from starlette.requests import Request

from .service import memory_add, memory_search, memory_list, memory_get, \
    memory_update, memory_delete, memory_clear, memory_stats

THIS_DIR = Path(__file__).resolve().parent
ADMIN_HTML_PATH = THIS_DIR.parent / "web" / "index.html"


async def handle_admin(request: Request):
    return FileResponse(ADMIN_HTML_PATH)


async def handle_list_memories(request: Request):
    ns = request.query_params.get("namespace")
    limit = int(request.query_params.get("limit", 100))
    offset = int(request.query_params.get("offset", 0))
    return Response(
        json.dumps(memory_list(namespace=ns, limit=limit, offset=offset), ensure_ascii=False, default=str),
        media_type="application/json",
    )


async def handle_get_memory(request: Request):
    mid = request.path_params.get("memory_id")
    result = memory_get(mid)
    if result is None:
        return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
    return Response(
        json.dumps(result, ensure_ascii=False, default=str),
        media_type="application/json",
    )


async def handle_add_memory(request: Request):
    body = await request.json()
    result = memory_add(body["text"], body.get("metadata"), body.get("namespace"))
    return Response(json.dumps(result), status_code=201, media_type="application/json")


async def handle_update_memory(request: Request):
    mid = request.path_params.get("memory_id")
    body = await request.json()
    result = memory_update(mid, body.get("text"), body.get("metadata"))
    if not result["success"]:
        return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
    return Response(json.dumps(result), media_type="application/json")


async def handle_delete_memory(request: Request):
    mid = request.path_params.get("memory_id")
    result = memory_delete(mid)
    if not result["success"]:
        return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
    return Response(json.dumps(result), media_type="application/json")


async def handle_clear_memories(request: Request):
    ns = request.query_params.get("namespace")
    result = memory_clear(namespace=ns)
    return Response(json.dumps(result), media_type="application/json")


async def handle_search_memories(request: Request):
    body = await request.json()
    results = memory_search(body["query"], int(body.get("k", 5)),
                            float(body.get("threshold", 0.0)), body.get("namespace"))
    return Response(
        json.dumps(results, ensure_ascii=False, default=str),
        media_type="application/json",
    )


async def handle_stats(request: Request):
    return Response(json.dumps(memory_stats(), ensure_ascii=False), media_type="application/json")


admin_routes = [
    Route("/admin/console", endpoint=handle_admin, methods=["GET"]),
    Route("/api/memories", endpoint=handle_list_memories, methods=["GET"]),
    Route("/api/memories", endpoint=handle_add_memory, methods=["POST"]),
    Route("/api/memories", endpoint=handle_clear_memories, methods=["DELETE"]),
    Route("/api/memories/search", endpoint=handle_search_memories, methods=["POST"]),
    Route("/api/memories/{memory_id}", endpoint=handle_get_memory, methods=["GET"]),
    Route("/api/memories/{memory_id}", endpoint=handle_update_memory, methods=["PUT"]),
    Route("/api/memories/{memory_id}", endpoint=handle_delete_memory, methods=["DELETE"]),
    Route("/api/stats", endpoint=handle_stats, methods=["GET"]),
]
