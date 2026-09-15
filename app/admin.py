import json
import sys
import os
import logging
import threading
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from starlette.responses import Response, FileResponse
from starlette.background import BackgroundTask
from starlette.routing import Route
from starlette.requests import Request

from .service import (
    memory_add, memory_search, memory_list, memory_get,
    memory_update, memory_delete, memory_clear, memory_stats, memory_sync,
    memory_namespaces, memory_migrate_namespace, memory_get_details, memory_summarize,
    memory_import_start, memory_import_status, memory_export_full,
    memory_export_namespace, memory_storage_paths, storage, memory_extract_keywords,
    IMPORT_MAX_BYTES,
)
from .audit import read_events as audit_read_events
from . import audit
from .embed import EmbeddingInputTooLong
from .jsonl_transfer import safe_filename_part, unlink_quietly

logger = logging.getLogger("admin")

THIS_DIR = Path(__file__).resolve().parent
ADMIN_HTML_PATH = THIS_DIR.parent / "web" / "index.html"


async def handle_admin(request: Request):
    logger.info("GET /admin/console")
    # No-store: the UI is edited frequently; always serve the latest HTML
    # instead of letting browsers serve stale cached copies.
    return FileResponse(ADMIN_HTML_PATH, headers={
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Expires": "0",
    })


async def handle_list_memories(request: Request):
    try:
        ns = request.query_params.get("namespace")
        limit = int(request.query_params.get("limit", 100))
        offset = int(request.query_params.get("offset", 0))
        sort_by = request.query_params.get("sort_by", "created_at")
        updated_after = request.query_params.get("updated_after")
        updated_before = request.query_params.get("updated_before")
        # keywords 支持多值 (?keywords=a&keywords=b) 和逗号分隔两种传法
        keywords = request.query_params.getlist("keywords")
        if keywords:
            merged = []
            for kw in keywords:
                merged.extend(kw.split(","))
            keywords = [k.strip() for k in merged if k.strip()]
        else:
            keywords = None
        logger.info("GET /api/memories namespace=%s limit=%d offset=%d sort_by=%s updated_after=%s updated_before=%s keywords=%s",
                    ns, limit, offset, sort_by, updated_after, updated_before, keywords)
        return Response(
            json.dumps(memory_list(namespace=ns, limit=limit, offset=offset, sort_by=sort_by,
                                   updated_after=updated_after, updated_before=updated_before,
                                   keywords=keywords),
                       ensure_ascii=False, default=str),
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


async def handle_get_details(request: Request):
    try:
        body = await request.json()
        ids = body.get("memory_ids", [])
        logger.info("POST /api/memories/details count=%d", len(ids))
        result = memory_get_details(ids)
        return Response(
            json.dumps(result, ensure_ascii=False, default=str),
            media_type="application/json",
        )
    except Exception as e:
        logger.error("POST /api/memories/details failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_summarize(request: Request):
    try:
        body = await request.json()
        ns = body.get("namespace")
        mode = body.get("mode", "missing")
        memory_id = body.get("memory_id")
        logger.info("POST /api/summarize namespace=%s mode=%s memory_id=%s", ns, mode, memory_id)
        result = memory_summarize(namespace=ns, mode=mode, memory_id=memory_id)
        return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
    except Exception as e:
        logger.error("POST /api/summarize failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_extract_keywords(request: Request):
    try:
        body = await request.json()
        ns = body.get("namespace")
        mode = body.get("mode", "missing")
        memory_id = body.get("memory_id")
        logger.info("POST /api/extract-keywords namespace=%s mode=%s memory_id=%s", ns, mode, memory_id)
        result = memory_extract_keywords(namespace=ns, mode=mode, memory_id=memory_id)
        return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
    except Exception as e:
        logger.error("POST /api/extract-keywords failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_add_memory(request: Request):
    try:
        body = await request.json()
        logger.info("POST /api/memories text_len=%d", len(body.get("text", "")))
        result = memory_add(body["text"], body.get("metadata"), body.get("namespace"),
                            body.get("summary"))
        return Response(json.dumps(result), status_code=201, media_type="application/json")
    except EmbeddingInputTooLong as e:
        logger.warning("POST /api/memories rejected: %s", e)
        return Response(
            json.dumps({"detail": str(e), "code": "embedding_input_too_long"}),
            status_code=413, media_type="application/json",
        )
    except Exception as e:
        logger.error("POST /api/memories failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_update_memory(request: Request):
    try:
        mid = request.path_params.get("memory_id")
        body = await request.json()
        logger.info("PUT /api/memories/%s", mid)
        result = memory_update(mid, body.get("text"), body.get("metadata"),
                               body.get("namespace"), body.get("summary"))
        if not result["success"]:
            return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
        return Response(json.dumps(result), media_type="application/json")
    except EmbeddingInputTooLong as e:
        logger.warning("PUT /api/memories/%s rejected: %s",
                       request.path_params.get("memory_id", ""), e)
        return Response(
            json.dumps({"detail": str(e), "code": "embedding_input_too_long"}),
            status_code=413, media_type="application/json",
        )
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


async def handle_import_memories(request: Request):
    temp_path = None
    started = time.perf_counter()
    original_name = "upload.jsonl"
    prefix = ""
    total = 0
    try:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise ValueError("multipart field 'file' is required")
        prefix = str(form.get("namespace_prefix") or "")
        handle = tempfile.NamedTemporaryFile(
            prefix="remember-me-import-", suffix=".jsonl", delete=False
        )
        temp_path = handle.name
        try:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > IMPORT_MAX_BYTES:
                    raise ValueError(f"import file exceeds {IMPORT_MAX_BYTES} bytes")
                handle.write(chunk)
        finally:
            handle.close()
        original_name = getattr(upload, "filename", None) or "upload.jsonl"
        logger.info("POST /api/import file=%s bytes=%d prefix=%s",
                    os.path.basename(original_name), total, prefix)
        result = memory_import_start(temp_path, original_name, prefix)
        temp_path = None  # background job owns cleanup
        return Response(json.dumps(result), status_code=202, media_type="application/json")
    except RuntimeError as e:
        unlink_quietly(temp_path)
        audit.record(
            "memory_import",
            {"filename": os.path.basename(original_name), "bytes": total,
             "namespace_prefix": prefix}, None,
            (time.perf_counter() - started) * 1000,
            error={"type": type(e).__name__, "message": str(e)},
        )
        return Response(json.dumps({"detail": str(e)}), status_code=409,
                        media_type="application/json")
    except (ValueError, TypeError) as e:
        unlink_quietly(temp_path)
        logger.warning("POST /api/import rejected: %s", e)
        audit.record(
            "memory_import",
            {"filename": os.path.basename(original_name), "bytes": total,
             "namespace_prefix": prefix}, None,
            (time.perf_counter() - started) * 1000,
            error={"type": type(e).__name__, "message": str(e)},
        )
        return Response(json.dumps({"detail": str(e)}, ensure_ascii=False),
                        status_code=413 if "exceeds" in str(e) else 400,
                        media_type="application/json")
    except Exception as e:
        unlink_quietly(temp_path)
        logger.error("POST /api/import failed: %s", e, exc_info=True)
        audit.record(
            "memory_import",
            {"filename": os.path.basename(original_name), "bytes": total,
             "namespace_prefix": prefix}, None,
            (time.perf_counter() - started) * 1000,
            error={"type": type(e).__name__, "message": str(e)},
        )
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_import_status(request: Request):
    result = memory_import_status(request.path_params["import_id"])
    if result is None:
        return Response(json.dumps({"detail": "Import job not found"}), status_code=404,
                        media_type="application/json")
    return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")


def _export_response(result, filename):
    return FileResponse(
        result["path"], filename=filename, media_type="application/x-ndjson",
        headers={"X-Memory-Count": str(result["records"]),
                 "X-Content-SHA256": result["sha256"]},
        background=BackgroundTask(unlink_quietly, result["path"]),
    )


async def handle_export_full(request: Request):
    started = time.perf_counter()
    fd, path = tempfile.mkstemp(prefix="remember-me-export-full-", suffix=".jsonl")
    os.close(fd)
    try:
        result = memory_export_full(path)
        audit.record("memory_export", {"type": "full"}, result,
                     (time.perf_counter() - started) * 1000)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return _export_response(result, f"remember-me-full-{stamp}.jsonl")
    except RuntimeError as exc:
        unlink_quietly(path)
        audit.record("memory_export", {"type": "full"}, None,
                     (time.perf_counter() - started) * 1000,
                     error={"type": type(exc).__name__, "message": str(exc)})
        return Response(json.dumps({"detail": str(exc)}), status_code=409,
                        media_type="application/json")
    except Exception as exc:
        unlink_quietly(path)
        logger.error("Full Export failed", exc_info=True)
        audit.record("memory_export", {"type": "full"}, None,
                     (time.perf_counter() - started) * 1000,
                     error={"type": type(exc).__name__, "message": str(exc)})
        return Response(json.dumps({"detail": "Internal error"}), status_code=500,
                        media_type="application/json")


async def handle_export_namespace(request: Request):
    started = time.perf_counter()
    path = None
    namespace = ""
    try:
        body = await request.json()
        namespace = body.get("namespace", "")
        fd, path = tempfile.mkstemp(prefix="remember-me-export-ns-", suffix=".jsonl")
        os.close(fd)
        result = memory_export_namespace(namespace, path)
        audit.record("memory_export", {"type": "namespace", "namespace": namespace}, result,
                     (time.perf_counter() - started) * 1000)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_ns = safe_filename_part(namespace)
        return _export_response(
            result, f"remember-me-namespace-{safe_ns}-{stamp}.jsonl"
        )
    except LookupError as exc:
        unlink_quietly(path)
        audit.record("memory_export", {"type": "namespace", "namespace": namespace}, None,
                     (time.perf_counter() - started) * 1000,
                     error={"type": type(exc).__name__, "message": str(exc)})
        return Response(json.dumps({"detail": str(exc)}), status_code=404,
                        media_type="application/json")
    except (ValueError, TypeError) as exc:
        unlink_quietly(path)
        audit.record("memory_export", {"type": "namespace", "namespace": namespace}, None,
                     (time.perf_counter() - started) * 1000,
                     error={"type": type(exc).__name__, "message": str(exc)})
        return Response(json.dumps({"detail": str(exc)}), status_code=400,
                        media_type="application/json")
    except Exception as exc:
        unlink_quietly(path)
        logger.error("Namespace Export failed", exc_info=True)
        audit.record("memory_export", {"type": "namespace", "namespace": namespace}, None,
                     (time.perf_counter() - started) * 1000,
                     error={"type": type(exc).__name__, "message": str(exc)})
        return Response(json.dumps({"detail": "Internal error"}), status_code=500,
                        media_type="application/json")


async def handle_search_memories(request: Request):
    try:
        body = await request.json()
        logger.info("POST /api/memories/search query_len=%d", len(body.get("query", "")))
        results = memory_search(body["query"], int(body.get("k", 5)),
                                float(body.get("threshold", 0.0)), body.get("namespace"),
                                keywords=body.get("keywords"),
                                categories=body.get("categories"))
        return Response(
            json.dumps(results, ensure_ascii=False, default=str),
            media_type="application/json",
        )
    except EmbeddingInputTooLong as e:
        logger.warning("POST /api/memories/search rejected: %s", e)
        return Response(
            json.dumps({"detail": str(e), "code": "embedding_input_too_long"}),
            status_code=413, media_type="application/json",
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


async def handle_storage_paths(request: Request):
    try:
        result = memory_storage_paths()
        return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
    except Exception as e:
        logger.error("GET /api/storage-paths failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


async def handle_restart(request: Request):
    """Restart the server process in place (same argv/env).

    Replies first so the browser gets a response, then re-execs after a short
    delay from a daemon thread. Connection drops briefly; clients reconnect.
    """
    logger.info("POST /api/restart requested")
    argv = [sys.executable, "-m", "app.server"] + sys.argv[1:]
    logger.info("restart: exec argv=%s", argv)

    def _do_restart():
        time.sleep(0.5)  # let the HTTP response flush first
        os.execv(sys.executable, argv)

    threading.Thread(target=_do_restart, daemon=True).start()
    return Response(json.dumps({"status": "restarting", "argv": argv}, ensure_ascii=False),
                    media_type="application/json")


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


async def handle_audit(request: Request):
    """Tool-call and background-work audit journal (newest first)."""
    try:
        try:
            limit = int(request.query_params.get("limit", "500"))
        except (TypeError, ValueError):
            limit = 500
        limit = max(1, min(limit, 5000))
        tool = request.query_params.get("tool") or None
        namespace = request.query_params.get("namespace") or None
        logger.info("GET /api/audit limit=%d tool=%s namespace=%s", limit, tool, namespace)
        events = audit_read_events(limit=limit, tool=tool, namespace=namespace)
        return Response(json.dumps(events, ensure_ascii=False), media_type="application/json")
    except Exception as e:
        logger.error("GET /api/audit failed: %s", e, exc_info=True)
        return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")


admin_routes = [
    Route("/admin/console", endpoint=handle_admin, methods=["GET"]),
    Route("/api/memories", endpoint=handle_list_memories, methods=["GET"]),
    Route("/api/memories", endpoint=handle_add_memory, methods=["POST"]),
    Route("/api/memories", endpoint=handle_clear_memories, methods=["DELETE"]),
    Route("/api/memories/search", endpoint=handle_search_memories, methods=["POST"]),
    Route("/api/memories/details", endpoint=handle_get_details, methods=["POST"]),
    Route("/api/memories/{memory_id}", endpoint=handle_get_memory, methods=["GET"]),
    Route("/api/memories/{memory_id}", endpoint=handle_update_memory, methods=["PUT"]),
    Route("/api/memories/{memory_id}", endpoint=handle_delete_memory, methods=["DELETE"]),
    Route("/api/stats", endpoint=handle_stats, methods=["GET"]),
    Route("/api/storage-paths", endpoint=handle_storage_paths, methods=["GET"]),
    Route("/api/import", endpoint=handle_import_memories, methods=["POST"]),
    Route("/api/import/{import_id}", endpoint=handle_import_status, methods=["GET"]),
    Route("/api/export/full", endpoint=handle_export_full, methods=["POST"]),
    Route("/api/export/namespace", endpoint=handle_export_namespace, methods=["POST"]),
    Route("/api/sync", endpoint=handle_sync, methods=["POST"]),
    Route("/api/restart", endpoint=handle_restart, methods=["POST"]),
    Route("/api/summarize", endpoint=handle_summarize, methods=["POST"]),
    Route("/api/extract-keywords", endpoint=handle_extract_keywords, methods=["POST"]),
    Route("/api/namespaces/migrate", endpoint=handle_migrate_namespace, methods=["POST"]),
    Route("/api/audit", endpoint=handle_audit, methods=["GET"]),
]
