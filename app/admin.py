import json
import sys
from pathlib import Path
from starlette.responses import Response, FileResponse
from starlette.routing import Route
from starlette.requests import Request


THIS_DIR = Path(__file__).resolve().parent
ADMIN_HTML_PATH = THIS_DIR.parent / "web" / "index.html"


async def handle_admin(request: Request):
    return FileResponse(ADMIN_HTML_PATH)


async def handle_list_memories(request: Request):
    from .server import storage, _strip_embedding
    ns = request.query_params.get("namespace")
    limit = int(request.query_params.get("limit", 100))
    offset = int(request.query_params.get("offset", 0))
    results = storage.list_memories(namespace=ns, limit=limit, offset=offset)
    return Response(
        json.dumps([_strip_embedding(r) for r in results], ensure_ascii=False, default=str),
        media_type="application/json",
    )


async def handle_get_memory(request: Request):
    from .server import storage, _strip_embedding
    mid = request.path_params.get("memory_id")
    result = storage.get(mid)
    if result is None:
        return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
    return Response(
        json.dumps(_strip_embedding(result), ensure_ascii=False, default=str),
        media_type="application/json",
    )


async def handle_add_memory(request: Request):
    from .server import storage, _embed
    body = await request.json()
    text = body["text"]
    eid = storage.add(
        text,
        _embed(text),
        metadata=body.get("metadata"),
        namespace=body.get("namespace", "default"),
    )
    return Response(json.dumps({"id": eid}), status_code=201, media_type="application/json")


async def handle_update_memory(request: Request):
    from .server import storage, _embed
    mid = request.path_params.get("memory_id")
    body = await request.json()
    text = body.get("text")
    embedding = _embed(text) if text else None
    ok = storage.update(mid, text=text, embedding=embedding, metadata=body.get("metadata"))
    if not ok:
        return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
    return Response(json.dumps({"success": True}), media_type="application/json")


async def handle_delete_memory(request: Request):
    from .server import storage
    mid = request.path_params.get("memory_id")
    ok = storage.delete(mid)
    if not ok:
        return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
    return Response(json.dumps({"success": True}), media_type="application/json")


async def handle_clear_memories(request: Request):
    from .server import storage
    ns = request.query_params.get("namespace")
    n = storage.clear(namespace=ns)
    return Response(json.dumps({"deleted": n}), media_type="application/json")


async def handle_search_memories(request: Request):
    from .server import storage, _embed, _strip_embedding
    body = await request.json()
    query = body["query"]
    k = int(body.get("k", 5))
    threshold = float(body.get("threshold", 0.0))
    ns = body.get("namespace")
    query_emb = _embed(query)
    results = storage.search(query_emb, k=k, threshold=threshold, namespace=ns)
    return Response(
        json.dumps([_strip_embedding(r) for r in results], ensure_ascii=False, default=str),
        media_type="application/json",
    )


async def handle_stats(request: Request):
    from .server import storage, EMBED_MODEL, OLLAMA_BASE_URL
    stats = storage.get_stats()
    stats["embed_model"] = EMBED_MODEL
    stats["ollama_base_url"] = OLLAMA_BASE_URL
    return Response(json.dumps(stats, ensure_ascii=False), media_type="application/json")


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
