import json
import sys
import logging
from pathlib import Path
from starlette.responses import Response, FileResponse
from starlette.routing import Route
from starlette.requests import Request

from .service import memory_add, memory_search, memory_list, memory_get, \
    memory_update, memory_delete, memory_clear, memory_stats, memory_sync, memory_recover, \
    memory_namespaces, memory_migrate_namespace

logger = logging.getLogger("admin")

THIS_DIR = Path(__file__).resolve().parent
ADMIN_HTML_PATH = THIS_DIR.parent / "web" / "index.html"


async def handle_admin(request: Request):
    logger.info("GET /admin/console")
    return FileResponse(ADMIN_HTML_PATH)


async def handle_list_memories(request: Request):
    try:
        ns = request.query_params.get("namespace")
        limit = int(request.query_params.get("limit", 100))
        offset = int(request.query_params.get("offset", 0))
        logger.info("GET /api/memories namespace=%s limit=%d offset=%d", ns, limit, offset)
        return Response(
            json.dumps(memory_list(namespace=ns, limit=limit, offset=offset), ensure_ascii=False, default=str),
            media_type="application/json",
        )
    except Exception as e:
        logger.error("GET /api/memories failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_get_memory(request: Request):
    try:
        mid = request.path_params.get("memory_id")
        logger.info("GET /api/memories/%s", mid)
        result = memory_get(mid)
        if result is None:
            return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
        return Response(
            json.dumps(result, ensure_ascii=False, default=str),
            media_type="application/json",
        )
    except Exception as e:
        logger.error("GET /api/memories/%s failed: %s", request.path_params.get("memory_id", ""), e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_add_memory(request: Request):
    try:
        body = await request.json()
        logger.info("POST /api/memories text_len=%d", len(body.get("text", "")))
        result = memory_add(body["text"], body.get("metadata"), body.get("namespace"))
        return Response(json.dumps(result), status_code=201, media_type="application/json")
    except Exception as e:
        logger.error("POST /api/memories failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_update_memory(request: Request):
    try:
        mid = request.path_params.get("memory_id")
        body = await request.json()
        logger.info("PUT /api/memories/%s", mid)
        result = memory_update(mid, body.get("text"), body.get("metadata"), body.get("namespace"))
        if not result["success"]:
            return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
        return Response(json.dumps(result), media_type="application/json")
    except Exception as e:
        logger.error("PUT /api/memories/%s failed: %s", request.path_params.get("memory_id", ""), e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_delete_memory(request: Request):
    try:
        mid = request.path_params.get("memory_id")
        logger.info("DELETE /api/memories/%s", mid)
        result = memory_delete(mid)
        if not result["success"]:
            return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
        return Response(json.dumps(result), media_type="application/json")
    except Exception as e:
        logger.error("DELETE /api/memories/%s failed: %s", request.path_params.get("memory_id", ""), e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_clear_memories(request: Request):
    try:
        ns = request.query_params.get("namespace")
        logger.info("DELETE /api/memories namespace=%s", ns)
        result = memory_clear(namespace=ns)
        return Response(json.dumps(result), media_type="application/json")
    except Exception as e:
        logger.error("DELETE /api/memories (clear) failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_search_memories(request: Request):
    try:
        body = await request.json()
        logger.info("POST /api/memories/search query_len=%d", len(body.get("query", "")))
        results = memory_search(body["query"], int(body.get("k", 5)),
                                float(body.get("threshold", 0.0)), body.get("namespace"))
        return Response(
            json.dumps(results, ensure_ascii=False, default=str),
            media_type="application/json",
        )
    except Exception as e:
        logger.error("POST /api/memories/search failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_stats(request: Request):
    try:
        logger.info("GET /api/stats")
        return Response(json.dumps(memory_stats(), ensure_ascii=False), media_type="application/json")
    except Exception as e:
        logger.error("GET /api/stats failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_sync(request: Request):
    try:
        logger.info("POST /api/sync")
        result = memory_sync()
        return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
    except Exception as e:
        logger.error("POST /api/sync failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_recover(request: Request):
    try:
        body = await request.json()
        src = body["source"]
        tgt = body["target"]
        logger.info("POST /api/recover source=%s target=%s", src, tgt)
        result = memory_recover(src, tgt)
        return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
    except Exception as e:
        logger.error("POST /api/recover failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_migrate_namespace(request: Request):
    try:
        body = await request.json()
        source = body["source"]
        target = body["target"]
        logger.info("POST /api/namespaces/migrate source=%s target=%s", source, target)
        result = memory_migrate_namespace(source, target)
        return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
    except Exception as e:
        logger.error("POST /api/namespaces/migrate failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


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
    Route("/api/sync", endpoint=handle_sync, methods=["POST"]),
    Route("/api/recover", endpoint=handle_recover, methods=["POST"]),
    Route("/api/namespaces/migrate", endpoint=handle_migrate_namespace, methods=["POST"]),
]
