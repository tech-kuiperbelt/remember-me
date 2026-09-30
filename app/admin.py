import json
import asyncio
import sys
import os
import logging
import threading
import time
import tempfile
import secrets
from datetime import datetime, timezone
from pathlib import Path
from starlette.responses import Response, FileResponse
from starlette.background import BackgroundTask
from starlette.routing import Route
from starlette.requests import Request

from .service import InvalidSearchFilter
from .calls import INLINE_RUNNER, ServiceClosing
from .embed import EmbeddingInputTooLong
from .lexical import LexicalQueryError
from .anchors import InvalidAnchors
from .namespace_policy import NamespaceNotFound, InvalidNamespace
from .jsonl_transfer import safe_filename_part, unlink_quietly
from .maintenance import MaintenanceRejected

logger = logging.getLogger("admin")

THIS_DIR = Path(__file__).resolve().parent
ADMIN_HTML_PATH = THIS_DIR.parent / "web" / "index.html"


def _namespace_not_found_response(exc: NamespaceNotFound) -> Response:
    """HTTP 400 with the same machine-readable envelope used by MCP."""
    available = ", ".join(exc.known) or "(none)"
    return Response(
        json.dumps({
            "detail": f"namespace not found: {exc.requested}. Available: {available}",
            "response_code": NamespaceNotFound.code,
            "response_meta": {
                "requested_namespace": exc.requested,
                "known_namespaces": exc.known,
            },
        }, ensure_ascii=False),
        status_code=400, media_type="application/json",
    )


def _invalid_namespace_response(exc: InvalidNamespace) -> Response:
    """HTTP 400 for namespace format/type validation failures."""
    return Response(
        json.dumps({
            "detail": str(exc),
            "response_code": "invalid_namespace",
            "response_meta": {"reason": str(exc)},
        }, ensure_ascii=False),
        status_code=400, media_type="application/json",
    )


class InvalidRequest(ValueError):
    """A failure parsing the public request, not an internal business error."""


def _invalid_request_response(exc):
    return Response(json.dumps({"detail": str(exc), "response_code": "invalid_request",
                               "response_meta": {"reason": str(exc)}}),
                    status_code=400, media_type="application/json")


def _invalid_anchors_response(exc):
    """HTTP 400 for strict metadata.anchors validation failures."""
    return Response(json.dumps({"detail": str(exc), "response_code": "invalid_anchors",
                               "response_meta": {"field": "metadata.anchors"}},
                              ensure_ascii=False),
                    status_code=400, media_type="application/json")


async def _request_object(request, required=(), string_fields=()):
    try:
        body = await request.json()
    except (ValueError, UnicodeError) as exc:
        raise InvalidRequest("request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise InvalidRequest("request body must be an object")
    for key in required:
        if key not in body:
            raise InvalidRequest(f"{key} is required")
    for key in string_fields:
        if key in body and body[key] is not None and not isinstance(body[key], str):
            raise InvalidRequest(f"{key} must be a string")
        if key in required and body.get(key) is None:
            raise InvalidRequest(f"{key} must be a string")
    if "metadata" in body and body["metadata"] is not None and not isinstance(body["metadata"], dict):
        raise InvalidRequest("metadata must be an object")
    return body


def _number(value, converter, field):
    try:
        return converter(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise InvalidRequest(f"{field} must be a valid number") from exc


def _export_response(result, filename):
    return FileResponse(
        result["path"], filename=filename, media_type="application/x-ndjson",
        headers={"X-Memory-Count": str(result["records"]),
                 "X-Content-SHA256": result["sha256"]},
        background=BackgroundTask(unlink_quietly, result["path"]),
    )


def _maintenance_response(exc):
    status = 503 if exc.code in ("maintenance_mode", "maintenance_switching",
                                 "maintenance_unavailable", "model_not_ready") else 409
    return Response(json.dumps({"detail": str(exc), "response_code": exc.code}),
                    status_code=status, media_type="application/json")


class AdminAPI:
    def _temp_dir(self):
        directory = Path((self.runtime.settings if self.runtime is not None else self.service.settings).TEMP_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory)

    def __init__(self, service=None, *, runtime=None):
        if service is None and runtime is None:
            raise ValueError("service or runtime is required")
        self._service = service
        self.runtime = runtime

    @property
    def service(self):
        return self._service if self._service is not None else self.runtime.service

    @property
    def audit(self):
        return self.runtime.audit if self.runtime is not None else self.service.audit

    def _call_runner(self):
        return self.runtime.calls if self.runtime is not None else INLINE_RUNNER

    async def _run(self, work, *, control=False, write=False):
        """Run synchronous business off the event loop via the runtime adapter.

        Ordinary reads/writes are admitted by ``_guard``. Mutating memory routes
        defer admission to the worker (``write=True``) so a call queued before
        maintenance entered is re-checked when it actually runs.
        """
        admission = None
        if self.runtime is not None and write and not control:
            admission = self.runtime.maintenance.access(write=True)
        def checked_work():
            if self.runtime is not None and self.runtime.state != "running":
                raise ServiceClosing("service is closing; no new work accepted")
            return work()

        return await self._call_runner().run(
            checked_work, control=control, admission=admission, wait_on_cancel=True)

    @staticmethod
    def _audit_context(request):
        state = request.scope.get("state", {}) if hasattr(request, "scope") else {}
        return {"source": "admin_http", "request_id": state.get("audit_request_id") or "rq_" + os.urandom(8).hex()}

    async def handle_maintenance_status(self, request):
        if self.runtime is None:
            return Response(json.dumps({"state": "unavailable", "action": None,
                                        "readers": 0, "writers": 0, "error": None}),
                            media_type="application/json")
        return Response(json.dumps({**self.runtime.maintenance.snapshot(),
                                    "model": self.runtime.model_status()}),
                        media_type="application/json")

    async def handle_model_status(self, request):
        if self.runtime is None:
            return Response(json.dumps({"ready": True, "status": "ready"}), media_type="application/json")
        return Response(json.dumps(self.runtime.model_status()), media_type="application/json")

    async def handle_model_config(self, request):
        if self.runtime is None:
            return Response(json.dumps({"detail": "configuration requires a runtime"}),
                            status_code=503, media_type="application/json")
        try:
            if request.method == "GET":
                result = await self._run(self.runtime.model_config, control=True)
            else:
                changes = await _request_object(request)
                result = await self._run(
                    lambda: self.runtime.save_model_config(changes), control=True)
            return Response(json.dumps(result), media_type="application/json")
        except MaintenanceRejected as exc:
            return _maintenance_response(exc)
        except (ValueError, OSError) as exc:
            return _invalid_request_response(exc)

    async def handle_model_retry(self, request):
        if self.runtime is None:
            return Response(json.dumps({"detail": "model preparation requires a runtime"}),
                            status_code=503, media_type="application/json")
        try:
            return Response(json.dumps(self.runtime.retry_model()), status_code=202,
                            media_type="application/json")
        except MaintenanceRejected as exc:
            return _maintenance_response(exc)

    async def handle_maintenance_enter(self, request):
        if self.runtime is None:
            return Response(json.dumps({"detail": "maintenance requires a runtime"}),
                            status_code=503, media_type="application/json")
        try:
            await asyncio.to_thread(self.runtime.enter_maintenance)
            self.audit.record("maintenance_enter", {}, self.runtime.maintenance.snapshot(), 0,
                              context=self._audit_context(request))
            return Response(json.dumps(self.runtime.maintenance.snapshot()),
                            media_type="application/json")
        except MaintenanceRejected as exc:
            return _maintenance_response(exc)

    async def handle_maintenance_exit(self, request):
        if self.runtime is None:
            return Response(json.dumps({"detail": "maintenance requires a runtime"}),
                            status_code=503, media_type="application/json")
        try:
            self.runtime.exit_maintenance()
            self.audit.record("maintenance_exit", {}, self.runtime.maintenance.snapshot(), 0,
                              context=self._audit_context(request))
            return Response(json.dumps(self.runtime.maintenance.snapshot()),
                            media_type="application/json")
        except MaintenanceRejected as exc:
            return _maintenance_response(exc)

    async def handle_recover(self, request):
        if self.runtime is None:
            return Response(json.dumps({"detail": "recover requires a runtime"}),
                            status_code=503, media_type="application/json")
        path = None
        try:
            self.runtime.maintenance.start_action("recover")
        except MaintenanceRejected as exc:
            return _maintenance_response(exc)
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise ValueError("multipart field 'file' is required")
            filename = getattr(upload, "filename", "") or ""
            if not filename.lower().endswith((".jsonl", ".ndjson")):
                raise ValueError("file must end with .jsonl or .ndjson")
            fd, path = tempfile.mkstemp(prefix="remember-me-recover-", suffix=".jsonl", dir=self._temp_dir())
            total = 0
            with os.fdopen(fd, "wb") as dest:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.service.settings.IMPORT_MAX_BYTES:
                        raise ValueError("recover file exceeds import size limit")
                    dest.write(chunk)
            result = self.runtime.start_recover(path, os.path.basename(filename), reserved=True)
            self.audit.record("online_recover_start", {"filename": os.path.basename(filename),
                                                       "bytes": total}, result, 0,
                              context=self._audit_context(request))
            path = None
            return Response(json.dumps(result), status_code=202, media_type="application/json")
        except (ValueError, RuntimeError) as exc:
            self.runtime.maintenance.finish_action(error=exc)
            return Response(json.dumps({"detail": str(exc)}), status_code=400,
                            media_type="application/json")
        except BaseException as exc:
            self.runtime.maintenance.finish_action(error=exc)
            logger.exception("recover upload failed")
            return Response(json.dumps({"detail": "Recover upload failed"}), status_code=500,
                            media_type="application/json")
        finally:
            if path is not None:
                unlink_quietly(path)

    async def handle_recover_status(self, request):
        if self.runtime is None:
            return Response(json.dumps({"detail": "recover requires a runtime"}),
                            status_code=503, media_type="application/json")
        job = self.runtime.recover_jobs.get(request.path_params["recover_id"])
        if job is None:
            return Response(json.dumps({"detail": "Recover job not found"}), status_code=404,
                            media_type="application/json")
        return Response(json.dumps(dict(job)), media_type="application/json")

    def _watch_import(self, import_id):
        # A started import owns the maintenance action even if its HTTP request
        # was cancelled before receiving the job ID.
        def await_import():
            try:
                while True:
                    status = self.service.memory_import_status(import_id)
                    if status is None or status["status"] in ("completed", "failed"):
                        break
                    threading.Event().wait(0.1)
                for worker in list(self.service._workers):
                    if worker.name == f"memory-import-{import_id}":
                        worker.join()
            finally:
                self.runtime.maintenance.finish_action(result={"import_id": import_id})
        threading.Thread(target=await_import, daemon=True,
                         name=f"maintenance-import-{import_id}").start()

    def _guard(self, endpoint, *, action=None, defer=False):
        async def guarded(request):
            if self.runtime is None:
                return await endpoint(request)
            gate = self.runtime.maintenance
            if action:
                try:
                    gate.start_action(action)
                except MaintenanceRejected as exc:
                    return _maintenance_response(exc)
                try:
                    response = await endpoint(request)
                    if action in ("restart", "shutdown") and response.status_code < 400:
                        background = response.background

                        async def finish_process_control():
                            try:
                                if background is not None:
                                    await background()
                            finally:
                                gate.finish_action()

                        response.background = BackgroundTask(finish_process_control)
                    elif action == "import" and response.status_code == 202:
                        self._watch_import(json.loads(response.body)["import_id"])
                    else:
                        if action == "sync" and response.status_code < 400:
                            marker = Path(f"{self.runtime.settings.STORAGE_PATH}.online-recover")
                            if marker.exists():
                                try:
                                    self.runtime._verify_recovered_replica()
                                except RuntimeError:
                                    pass
                                else:
                                    marker.unlink()
                                    gate.clear_error()
                        gate.finish_action()
                    return response
                except BaseException as exc:
                    import_id = request.scope.get("_started_import_id") if action == "import" else None
                    if import_id:
                        self._watch_import(import_id)
                    else:
                        gate.finish_action(error=exc)
                    raise
            if defer:
                # Reject early (before parsing) while the work still re-checks
                # admission inside the worker, so a call queued behind slow
                # inference cannot bypass maintenance that entered afterwards.
                try:
                    gate.check(write=True)
                except MaintenanceRejected as exc:
                    return _maintenance_response(exc)
                try:
                    return await endpoint(request)
                except MaintenanceRejected as exc:
                    return _maintenance_response(exc)
            try:
                with gate.access():
                    return await endpoint(request)
            except MaintenanceRejected as exc:
                return _maintenance_response(exc)
        return guarded

    async def handle_admin(self, request: Request):
        logger.info("GET /admin/console")
        # No-store: the UI is edited frequently; always serve the latest HTML
        # instead of letting browsers serve stale cached copies.
        return FileResponse(ADMIN_HTML_PATH, headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Expires": "0",
        })

    async def handle_list_memories(self, request: Request):
        try:
            ns = request.query_params.get("namespace")
            limit = _number(request.query_params.get("limit", 100), int, "limit")
            offset = _number(request.query_params.get("offset", 0), int, "offset")
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
            result = await self._run(lambda: self.service.memory_list(
                namespace=ns, limit=limit, offset=offset, sort_by=sort_by,
                updated_after=updated_after, updated_before=updated_before,
                keywords=keywords))
            return Response(
                json.dumps(result, ensure_ascii=False, default=str),
                media_type="application/json",
            )
        except NamespaceNotFound as e:
            logger.warning("GET /api/memories rejected: %s", e)
            return _namespace_not_found_response(e)
        except InvalidNamespace as e:
            logger.warning("GET /api/memories invalid request: %s", e)
            return _invalid_namespace_response(e)
        except InvalidRequest as e:
            return _invalid_request_response(e)
        except Exception as e:
            logger.error("GET /api/memories failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_get_memory(self, request: Request):
        try:
            mid = request.path_params.get("memory_id")
            logger.info("GET /api/memories/%s", mid)
            result = await self._run(lambda: self.service.memory_get(mid))
            if result is None:
                return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
            return Response(
                json.dumps(result, ensure_ascii=False, default=str),
                media_type="application/json",
            )
        except Exception as e:
            logger.error("GET /api/memories/%s failed: %s", request.path_params.get("memory_id", ""), e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_get_details(self, request: Request):
        try:
            body = await request.json()
            ids = body.get("memory_ids", [])
            logger.info("POST /api/memories/details count=%d", len(ids))
            result = await self._run(lambda: self.service.memory_get_details(ids))
            return Response(
                json.dumps(result, ensure_ascii=False, default=str),
                media_type="application/json",
            )
        except Exception as e:
            logger.error("POST /api/memories/details failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_add_memory(self, request: Request):
        try:
            body = await _request_object(request, required=("text",), string_fields=("text",))
            logger.info("POST /api/memories text_len=%d", len(body.get("text", "")))
            result = await self._run(lambda: self.service.memory_add(
                body["text"], body.get("metadata"), body.get("namespace"),
                body.get("summary"), body.get("confirm_codes")), write=True)
            return Response(json.dumps(result), status_code=201, media_type="application/json")
        except EmbeddingInputTooLong as e:
            logger.warning("POST /api/memories rejected: %s", e)
            return Response(
                json.dumps({"detail": str(e), "code": "embedding_input_too_long"}),
                status_code=413, media_type="application/json",
            )
        except NamespaceNotFound as e:
            logger.warning("POST /api/memories rejected: %s", e)
            return _namespace_not_found_response(e)
        except InvalidNamespace as e:
            logger.warning("POST /api/memories invalid namespace: %s", e)
            return _invalid_namespace_response(e)
        except InvalidAnchors as e:
            logger.warning("POST /api/memories invalid anchors: %s", e)
            return _invalid_anchors_response(e)
        except InvalidRequest as e:
            return _invalid_request_response(e)
        except MaintenanceRejected:
            raise
        except Exception as e:
            logger.error("POST /api/memories failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_update_memory(self, request: Request):
        try:
            mid = request.path_params.get("memory_id")
            body = await _request_object(request, required=(), string_fields=("text",))
            logger.info("PUT /api/memories/%s", mid)
            result = await self._run(lambda: self.service.memory_update(
                mid, body.get("text"), body.get("metadata"),
                body.get("namespace"), body.get("summary"),
                body.get("confirm_codes")), write=True)
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
        except NamespaceNotFound as e:
            logger.warning("PUT /api/memories/%s rejected: %s",
                           request.path_params.get("memory_id", ""), e)
            return _namespace_not_found_response(e)
        except InvalidNamespace as e:
            logger.warning("PUT /api/memories/%s invalid namespace: %s",
                           request.path_params.get("memory_id", ""), e)
            return _invalid_namespace_response(e)
        except InvalidAnchors as e:
            logger.warning("PUT /api/memories/%s invalid anchors: %s",
                           request.path_params.get("memory_id", ""), e)
            return _invalid_anchors_response(e)
        except InvalidRequest as e:
            return _invalid_request_response(e)
        except MaintenanceRejected:
            raise
        except Exception as e:
            logger.error("PUT /api/memories/%s failed: %s", request.path_params.get("memory_id", ""), e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_delete_memory(self, request: Request):
        try:
            mid = request.path_params.get("memory_id")
            logger.info("DELETE /api/memories/%s", mid)
            result = await self._run(lambda: self.service.memory_delete(mid), write=True)
            if not result["success"]:
                return Response(json.dumps({"detail": "Not found"}), status_code=404, media_type="application/json")
            return Response(json.dumps(result), media_type="application/json")
        except MaintenanceRejected:
            raise
        except Exception as e:
            logger.error("DELETE /api/memories/%s failed: %s", request.path_params.get("memory_id", ""), e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_clear_memories(self, request: Request):
        try:
            ns = request.query_params.get("namespace")
            logger.info("DELETE /api/memories namespace=%s", ns)
            result = await self._run(lambda: self.service.memory_clear(namespace=ns))
            return Response(json.dumps(result), media_type="application/json")
        except Exception as e:
            logger.error("DELETE /api/memories (clear) failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_import_memories(self, request: Request):
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
                prefix="remember-me-import-", suffix=".jsonl", delete=False, dir=self._temp_dir()
            )
            temp_path = handle.name
            try:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.service.settings.IMPORT_MAX_BYTES:
                        raise ValueError(f"import file exceeds {self.service.settings.IMPORT_MAX_BYTES} bytes")
                    handle.write(chunk)
            finally:
                handle.close()
            original_name = getattr(upload, "filename", None) or "upload.jsonl"
            logger.info("POST /api/import file=%s bytes=%d prefix=%s",
                        os.path.basename(original_name), total, prefix)
            def start_import():
                result = self.service.memory_import_start(temp_path, original_name, prefix)
                request.scope["_started_import_id"] = result["import_id"]
                return result
            result = await self._run(start_import)
            temp_path = None  # background job owns cleanup
            return Response(json.dumps(result), status_code=202, media_type="application/json")
        except asyncio.CancelledError:
            if not request.scope.get("_started_import_id"):
                unlink_quietly(temp_path)
            raise
        except RuntimeError as e:
            unlink_quietly(temp_path)
            self.audit.record(
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
            self.audit.record(
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
            self.audit.record(
                "memory_import",
                {"filename": os.path.basename(original_name), "bytes": total,
                 "namespace_prefix": prefix}, None,
                (time.perf_counter() - started) * 1000,
                error={"type": type(e).__name__, "message": str(e)},
            )
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_import_status(self, request: Request):
        result = self.service.memory_import_status(request.path_params["import_id"])
        if result is None:
            return Response(json.dumps({"detail": "Import job not found"}), status_code=404,
                            media_type="application/json")
        return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")

    async def _run_export(self, path, operation, arguments, context):
        """The worker owns the completion audit; the response owns a ready file."""
        def export():
            started = time.perf_counter()
            result, error = None, None
            try:
                result = operation(path)
                return result
            except BaseException as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
                raise
            finally:
                self.audit.record("memory_export", arguments, result,
                                  (time.perf_counter() - started) * 1000,
                                  error=error, context=context)
        try:
            return await self._run(export)
        except BaseException:
            # _run drains a running worker before propagating cancellation, so
            # cleanup cannot race a writer that recreates the exported file.
            unlink_quietly(path)
            raise

    async def handle_export_full(self, request: Request):
        fd, path = tempfile.mkstemp(prefix="remember-me-export-full-", suffix=".jsonl", dir=self._temp_dir())
        os.close(fd)
        try:
            result = await self._run_export(
                path, self.service.memory_export_full, {"type": "full"},
                self._audit_context(request))
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            return _export_response(result, f"remember-me-full-{stamp}.jsonl")
        except RuntimeError as exc:
            unlink_quietly(path)
            return Response(json.dumps({"detail": str(exc)}), status_code=409,
                            media_type="application/json")
        except Exception:
            unlink_quietly(path)
            logger.error("Full Export failed", exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500,
                            media_type="application/json")

    async def handle_export_namespace(self, request: Request):
        path = None
        namespace = ""
        export_started = False
        started = time.perf_counter()
        context = self._audit_context(request)
        try:
            body = await request.json()
            namespace = body.get("namespace", "")
            fd, path = tempfile.mkstemp(prefix="remember-me-export-ns-", suffix=".jsonl", dir=self._temp_dir())
            os.close(fd)
            export_started = True
            result = await self._run_export(
                path, lambda target: self.service.memory_export_namespace(namespace, target),
                {"type": "namespace", "namespace": namespace}, context)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            return _export_response(
                result, f"remember-me-namespace-{safe_filename_part(namespace)}-{stamp}.jsonl")
        except Exception as exc:
            unlink_quietly(path)
            if not export_started:
                self.audit.record("memory_export", {"type": "namespace", "namespace": namespace},
                                  None, (time.perf_counter() - started) * 1000,
                                  error={"type": type(exc).__name__, "message": str(exc)}, context=context)
            if isinstance(exc, LookupError):
                status, detail = 404, str(exc)
            elif isinstance(exc, (ValueError, TypeError)):
                status, detail = 400, str(exc)
            else:
                logger.error("Namespace Export failed", exc_info=True)
                status, detail = 500, "Internal error"
            return Response(json.dumps({"detail": detail}), status_code=status,
                            media_type="application/json")

    async def handle_search_memories(self, request: Request):
        try:
            body = await _request_object(request, required=("query",), string_fields=("query",))
            logger.info("POST /api/memories/search query_len=%d", len(body.get("query", "")))
            results = await self._run(lambda: self.service.memory_search(
                body["query"], _number(body.get("k", 5), int, "k"),
                _number(body.get("threshold", 0.0), float, "threshold"), body.get("namespace"),
                keywords=body.get("keywords"),
                categories=body.get("categories")))
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
        except NamespaceNotFound as e:
            logger.warning("POST /api/memories/search rejected: %s", e)
            return _namespace_not_found_response(e)
        except InvalidNamespace as e:
            logger.warning("POST /api/memories/search invalid namespace: %s", e)
            return _invalid_namespace_response(e)
        except (InvalidRequest, InvalidSearchFilter) as e:
            return _invalid_request_response(e)
        except Exception as e:
            logger.error("POST /api/memories/search failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_search_lexical(self, request: Request):
        try:
            body = await _request_object(request, required=("query",), string_fields=("query",))
            logger.info("POST /api/memories/search/lexical query_len=%d", len(body.get("query", "")))
            results = await self._run(lambda: self.service.memory_search_lexical(
                body["query"], _number(body.get("k", 10), int, "k"), body.get("namespace")))
            return Response(
                json.dumps(results, ensure_ascii=False, default=str),
                media_type="application/json",
            )
        except NamespaceNotFound as e:
            logger.warning("POST /api/memories/search/lexical rejected: %s", e)
            return _namespace_not_found_response(e)
        except InvalidNamespace as e:
            logger.warning("POST /api/memories/search/lexical invalid namespace: %s", e)
            return _invalid_namespace_response(e)
        except LexicalQueryError as e:
            logger.warning("POST /api/memories/search/lexical invalid query: %s", e)
            return Response(
                json.dumps({"detail": str(e), "response_code": "invalid_query"},
                           ensure_ascii=False),
                status_code=400, media_type="application/json",
            )
        except (InvalidRequest, InvalidSearchFilter) as e:
            return _invalid_request_response(e)
        except Exception as e:
            logger.error("POST /api/memories/search/lexical failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500,
                            media_type="application/json")

    async def handle_search_anchor(self, request: Request):
        try:
            body = await _request_object(request, required=("query",), string_fields=("query",))
            logger.info("POST /api/memories/search/anchor query_len=%d", len(body.get("query", "")))
            results = await self._run(lambda: self.service.memory_search_anchor(
                body["query"], _number(body.get("k", 10), int, "k"), body.get("namespace")))
            return Response(
                json.dumps(results, ensure_ascii=False, default=str),
                media_type="application/json",
            )
        except NamespaceNotFound as e:
            logger.warning("POST /api/memories/search/anchor rejected: %s", e)
            return _namespace_not_found_response(e)
        except InvalidNamespace as e:
            logger.warning("POST /api/memories/search/anchor invalid namespace: %s", e)
            return _invalid_namespace_response(e)
        except LexicalQueryError as e:
            logger.warning("POST /api/memories/search/anchor invalid query: %s", e)
            return Response(
                json.dumps({"detail": str(e), "response_code": "invalid_query"},
                           ensure_ascii=False),
                status_code=400, media_type="application/json",
            )
        except (InvalidRequest, InvalidSearchFilter) as e:
            return _invalid_request_response(e)
        except Exception as e:
            logger.error("POST /api/memories/search/anchor failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500,
                            media_type="application/json")

    async def handle_keywords(self, request: Request):
        started = time.perf_counter()
        body, result, error = {}, None, None
        try:
            body = await request.json()
            allowed = {"namespace", "mode", "queries", "limit", "offset", "sort_by", "min_score"}
            if not isinstance(body, dict) or set(body) - allowed:
                raise ValueError("expected keyword query object with supported parameters")
            result = await self._run(lambda: self.service.memory_list_keywords(**body))
            result["keyword_query_id"] = "kq_" + secrets.token_hex(8)
            return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
        except NamespaceNotFound as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            response = _namespace_not_found_response(exc)
            result = json.loads(response.body)
            return response
        except ValueError as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            return Response(json.dumps({"detail": str(exc)}), status_code=400, media_type="application/json")
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            return Response(json.dumps({"detail": str(exc)}), status_code=500, media_type="application/json")
        finally:
            self.audit.record("list_keywords", body if isinstance(body, dict) else {}, result,
                         (time.perf_counter() - started) * 1000, error=error,
                         context=self._audit_context(request))

    async def handle_stats(self, request: Request):
        try:
            logger.info("GET /api/stats")
            stats = await self._run(lambda: self.service.memory_stats())
            return Response(json.dumps(stats, ensure_ascii=False), media_type="application/json")
        except Exception as e:
            logger.error("GET /api/stats failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_sync(self, request: Request):
        try:
            logger.info("POST /api/sync")
            result = await self._run(lambda: self.service.memory_sync())
            return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
        except Exception as e:
            logger.error("POST /api/sync failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_storage_paths(self, request: Request):
        try:
            if self.runtime is not None and self.service is None:
                s = self.runtime.settings
                result = {"backend": s.STORAGE_BACKEND}
                if s.STORAGE_BACKEND == "dual":
                    relative = s.environment.get("MEMORY_PATHS_ACTIVE") == "1"
                    result.update(primary_path=os.path.relpath(s.STORAGE_PATH, s.DATA_DIR) if relative else s.STORAGE_PATH,
                                  secondary_path=os.path.relpath(s.REPLICA_PATH, s.DATA_DIR) if relative else s.REPLICA_PATH)
            else:
                result = await self._run(lambda: self.service.memory_storage_paths())
            return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
        except Exception as e:
            logger.error("GET /api/storage-paths failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_restart(self, request):
        return self._shutdown_response(restart=True)

    async def handle_shutdown(self, request):
        return self._shutdown_response(restart=False)

    def _shutdown_response(self, restart):
        if self.runtime is None:
            return Response(json.dumps({"detail": "process control unavailable"}),
                            status_code=503, media_type="application/json")
        argv = [sys.executable, "-m", "app.server"] + sys.argv[1:]
        data = {"status": "restarting" if restart else "stopping"}
        if restart:
            data["argv"] = argv
        # Starlette awaits this callback only after sending the complete response.
        async def request_shutdown():
            self.runtime.request_shutdown(restart=restart)
        return Response(json.dumps(data), media_type="application/json",
                        background=BackgroundTask(request_shutdown))

    async def handle_migrate_namespace(self, request: Request):
        try:
            body = await request.json()
            source = body["source"]
            target = body["target"]
            logger.info("POST /api/namespaces/migrate source=%s target=%s", source, target)
            result = await self._run(lambda: self.service.memory_migrate_namespace(source, target))
            return Response(json.dumps(result, ensure_ascii=False), media_type="application/json")
        except ValueError as e:
            logger.warning("POST /api/namespaces/migrate rejected: %s", e)
            return Response(json.dumps({"detail": str(e)}, ensure_ascii=False),
                            status_code=400, media_type="application/json")
        except Exception as e:
            logger.error("POST /api/namespaces/migrate failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    async def handle_audit(self, request: Request):
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
            events = await self._run(lambda: self.audit.read_events(
                limit=limit, tool=tool, namespace=namespace))
            return Response(json.dumps(events, ensure_ascii=False), media_type="application/json")
        except Exception as e:
            logger.error("GET /api/audit failed: %s", e, exc_info=True)
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    @property
    def routes(self):
        routes = [
            Route("/admin/console", endpoint=self.handle_admin, methods=["GET"]),
            Route("/api/memories", endpoint=self.handle_list_memories, methods=["GET"]),
            Route("/api/memories", endpoint=self.handle_add_memory, methods=["POST"]),
            Route("/api/memories", endpoint=self.handle_clear_memories, methods=["DELETE"]),
            Route("/api/memories/search", endpoint=self.handle_search_memories, methods=["POST"]),
            Route("/api/memories/search/lexical", endpoint=self.handle_search_lexical, methods=["POST"]),
            Route("/api/memories/search/anchor", endpoint=self.handle_search_anchor, methods=["POST"]),
            Route("/api/memories/details", endpoint=self.handle_get_details, methods=["POST"]),
            Route("/api/memories/{memory_id}", endpoint=self.handle_get_memory, methods=["GET"]),
            Route("/api/memories/{memory_id}", endpoint=self.handle_update_memory, methods=["PUT"]),
            Route("/api/memories/{memory_id}", endpoint=self.handle_delete_memory, methods=["DELETE"]),
            Route("/api/keywords/query", endpoint=self.handle_keywords, methods=["POST"]),
            Route("/api/stats", endpoint=self.handle_stats, methods=["GET"]),
            Route("/api/storage-paths", endpoint=self.handle_storage_paths, methods=["GET"]),
            Route("/api/import", endpoint=self.handle_import_memories, methods=["POST"]),
            Route("/api/import/{import_id}", endpoint=self.handle_import_status, methods=["GET"]),
            Route("/api/export/full", endpoint=self.handle_export_full, methods=["POST"]),
            Route("/api/export/namespace", endpoint=self.handle_export_namespace, methods=["POST"]),
            Route("/api/sync", endpoint=self.handle_sync, methods=["POST"]),
            Route("/api/shutdown", endpoint=self.handle_shutdown, methods=["POST"]),
            Route("/api/restart", endpoint=self.handle_restart, methods=["POST"]),
            Route("/api/namespaces/migrate", endpoint=self.handle_migrate_namespace, methods=["POST"]),
            Route("/api/audit", endpoint=self.handle_audit, methods=["GET"]),
            Route("/api/maintenance", endpoint=self.handle_maintenance_status, methods=["GET"]),
            Route("/api/model", endpoint=self.handle_model_status, methods=["GET"]),
            Route("/api/model/retry", endpoint=self.handle_model_retry, methods=["POST"]),
            Route("/api/config/model", endpoint=self.handle_model_config, methods=["GET", "POST"]),
            Route("/api/config", endpoint=self.handle_model_config, methods=["GET", "POST"]),
            Route("/api/maintenance/enter", endpoint=self.handle_maintenance_enter, methods=["POST"]),
            Route("/api/maintenance/exit", endpoint=self.handle_maintenance_exit, methods=["POST"]),
            Route("/api/recover", endpoint=self.handle_recover, methods=["POST"]),
            Route("/api/recover/{recover_id}", endpoint=self.handle_recover_status, methods=["GET"]),
        ]
        actions = {
            ("/api/memories", "DELETE"): "clear",
            ("/api/import", "POST"): "import",
            ("/api/export/full", "POST"): "export_full",
            ("/api/export/namespace", "POST"): "export_namespace",
            ("/api/sync", "POST"): "sync",
            ("/api/shutdown", "POST"): "shutdown",
            ("/api/restart", "POST"): "restart",
            ("/api/namespaces/migrate", "POST"): "migrate_namespace",
        }
        exempt = {"/admin/console", "/api/audit", "/api/storage-paths",
                  "/api/model", "/api/model/retry", "/api/config/model", "/api/config",
                  "/api/maintenance",
                  "/api/maintenance/enter", "/api/maintenance/exit",
                  "/api/recover", "/api/recover/{recover_id}"}
        # Mutating memory routes defer admission into the worker so a call queued
        # behind slow inference is re-checked after maintenance entered.
        deferred_writes = {
            ("/api/memories", "POST"),
            ("/api/memories/{memory_id}", "PUT"),
            ("/api/memories/{memory_id}", "DELETE"),
        }
        guarded = []
        for route in routes:
            method = next(iter(route.methods))
            action = actions.get((route.path, method))
            if action:
                endpoint = self._guard(route.endpoint, action=action)
            elif route.path in exempt:
                endpoint = route.endpoint
            elif (route.path, method) in deferred_writes:
                endpoint = self._guard(route.endpoint, defer=True)
            else:
                endpoint = self._guard(route.endpoint)
            guarded.append(Route(route.path, endpoint=endpoint, methods=list(route.methods)))
        return guarded


def create_admin_routes(service=None, *, runtime=None):
    return AdminAPI(service, runtime=runtime).routes
