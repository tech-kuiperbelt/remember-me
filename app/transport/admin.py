import json
import asyncio
import sys
import os
import functools
import logging
import time
import tempfile
import secrets
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from starlette.responses import Response, FileResponse
from starlette.background import BackgroundTask
from starlette.routing import Route
from starlette.requests import Request

from app.service import InvalidSearchFilter
from app.operations.calls import INLINE_RUNNER, ServiceClosing
from app.embedding.embed import EmbeddingInputTooLong
from app.domain.lexical import LexicalQueryError
from app.domain.anchors import InvalidAnchors
from app.domain.namespace_policy import NamespaceNotFound, InvalidNamespace
from app.storage.jsonl_transfer import safe_filename_part, unlink_quietly
from app.operations.maintenance import MaintenanceRejected
from app.config.configuration import PROJECT_ROOT

logger = logging.getLogger("admin")

ADMIN_HTML_PATH = PROJECT_ROOT / "web" / "index.html"
ADMIN_ASSET_ROOT = (PROJECT_ROOT / "web" / "static").resolve()

# Frontend assets may only be served from the static root and with these
# extensions. The mapping also pins the response media type so a wrong
# extension cannot smuggle arbitrary content into a script/style context.
ASSET_MEDIA_TYPES = {
    ".css": "text/css",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}


def json_response(data, status=200):
    return Response(json.dumps(data, ensure_ascii=False, default=str),
                    status_code=status, media_type="application/json")


def error_response(status, detail, *, code=None, response_meta=None):
    payload = {"detail": detail}
    if code is not None:
        payload["response_code"] = code
    if response_meta is not None:
        payload["response_meta"] = response_meta
    return json_response(payload, status)


def _namespace_not_found_response(exc: NamespaceNotFound) -> Response:
    """HTTP 400 with the same machine-readable envelope used by MCP."""
    available = ", ".join(exc.known) or "(none)"
    return error_response(
        400,
        f"namespace not found: {exc.requested}. Available: {available}",
        code=NamespaceNotFound.code,
        response_meta={"requested_namespace": exc.requested,
                       "known_namespaces": exc.known},
    )


def _invalid_namespace_response(exc: InvalidNamespace) -> Response:
    """HTTP 400 for namespace format/type validation failures."""
    return error_response(400, str(exc), code="invalid_namespace",
                          response_meta={"reason": str(exc)})


class InvalidRequest(ValueError):
    """A failure parsing the public request, not an internal business error."""


def _invalid_request_response(exc):
    return error_response(400, str(exc), code="invalid_request",
                          response_meta={"reason": str(exc)})


def _invalid_anchors_response(exc):
    """HTTP 400 for strict metadata.anchors validation failures."""
    return error_response(400, str(exc), code="invalid_anchors",
                          response_meta={"field": "metadata.anchors"})


def _embedding_too_long_response(exc):
    # HTTP contract uses ``code`` (not ``response_code``) for this endpoint family.
    return json_response({"detail": str(exc), "code": "embedding_input_too_long"}, status=413)


# Exact-type domain exception mapping shared by the standard handlers. An
# unrecognised exception (including a bare ValueError) stays a 500 unless a
# route explicitly opts into mapping it.
_DOMAIN_ERROR_RESPONSES = (
    (NamespaceNotFound, _namespace_not_found_response),
    (InvalidNamespace, _invalid_namespace_response),
    (InvalidAnchors, _invalid_anchors_response),
    (EmbeddingInputTooLong, _embedding_too_long_response),
    (InvalidRequest, _invalid_request_response),
    (InvalidSearchFilter, _invalid_request_response),
    (LexicalQueryError, lambda exc: error_response(400, str(exc), code="invalid_query")),
)


def domain_error_response(exc):
    for exc_type, builder in _DOMAIN_ERROR_RESPONSES:
        if isinstance(exc, exc_type):
            return builder(exc)
    return None


def internal_error_response(exc, *, label="request"):
    """Return the shared 500 payload, logging exactly one traceback at the boundary."""
    logger.error("%s failed: %s", label, exc, exc_info=True)
    return error_response(500, "Internal error")


def _maps_domain_errors(endpoint=None, *, domain=True, extras=()):
    """Map known domain exceptions to their HTTP response at the handler edge.

    Keeps unknown exceptions as a single logged 500. Applied per standard
    handler so direct calls and routed calls behave identically; handlers with
    bespoke cleanup/audit (import/export/recover/keywords/config) are not
    decorated. ``domain=False`` skips the shared domain mapping for a route that
    must keep its own exception shape (e.g. namespace migrate).
    """
    def decorate(fn):
        label = getattr(fn, "__name__", "request")

        @functools.wraps(fn)
        async def wrapped(self, request):
            try:
                return await fn(self, request)
            except MaintenanceRejected:
                raise
            except Exception as exc:
                if domain:
                    response = domain_error_response(exc)
                    if response is not None:
                        return response
                for exc_type, builder in extras:
                    if isinstance(exc, exc_type):
                        return builder(exc)
                return internal_error_response(exc, label=label)
        return wrapped
    if endpoint is None:
        return decorate
    return decorate(endpoint)


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


@dataclass(frozen=True)
class _Route:
    """One admin route and its explicitly declared access/error policy.

    policy: ``static`` (console HTML), ``asset`` (frontend file under
    ``web/static``), ``read`` (reader admission), ``write`` (deferred writer
    admission), ``action`` (single maintenance action), ``control`` and
    ``exempt`` (no admission wrapper). errors: ``boundary`` when the handler maps
    domain exceptions via ``_maps_domain_errors``; ``none`` when it keeps its own
    cleanup/audit handling.
    """

    path: str
    methods: tuple
    handler: str
    policy: str
    action: str | None = None
    errors: str = "boundary"


class AdminAPI:
    def _temp_dir(self):
        directory = Path((self.runtime.settings if self.runtime is not None else self.service.settings).TEMP_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory)

    def __init__(self, service=None, *, runtime=None, debug=False):
        if service is None and runtime is None:
            raise ValueError("service or runtime is required")
        self._service = service
        self.runtime = runtime
        self.debug = debug

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
        job = self.runtime.recover_status(request.path_params["recover_id"])
        if job is None:
            return Response(json.dumps({"detail": "Recover job not found"}), status_code=404,
                            media_type="application/json")
        return Response(json.dumps(dict(job)), media_type="application/json")

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
                    else:
                        gate.finish_action()
                    return response
                except BaseException as exc:
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
        html = await self._run(lambda: ADMIN_HTML_PATH.read_text(encoding="utf-8"), control=True)
        html = html.replace('data-ui-debug="false"',
                            'data-ui-debug="true"' if self.debug else 'data-ui-debug="false"', 1)
        return Response(html, media_type="text/html", headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Expires": "0",
        })

    async def handle_asset(self, request: Request):
        """Serve one frontend asset from the static root.

        The requested path is resolved and must stay inside ``web/static/``; only
        the whitelisted extensions are served. Anything else is a plain 404 so the
        endpoint never leaks files or serves attacker-controlled content types.
        """
        requested = request.path_params.get("asset", "")
        target = (ADMIN_ASSET_ROOT / requested).resolve()
        media_type = ASSET_MEDIA_TYPES.get(target.suffix.lower())
        if (media_type is None or not target.is_file()
                or not target.is_relative_to(ADMIN_ASSET_ROOT)):
            return Response("Not found", status_code=404)
        return FileResponse(target, media_type=media_type, headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Expires": "0",
        })

    @_maps_domain_errors
    async def handle_list_memories(self, request: Request):
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
        return json_response(result)

    @_maps_domain_errors
    async def handle_list_memories_page(self, request: Request):
        query = request.query_params
        scalars = {"namespace", "limit", "offset", "sort_by", "updated_after", "updated_before"}
        if set(query) - scalars - {"keywords", "categories"}:
            raise InvalidRequest("Unknown memory page parameter")
        if any(len(query.getlist(key)) > 1 for key in scalars):
            raise InvalidRequest("Scalar page parameters must not be repeated")
        numbers = {}
        for key, default in (("limit", "20"), ("offset", "0")):
            raw = query.get(key, default)
            if not re.fullmatch(r"[0-9]{1,19}", raw):
                raise InvalidRequest(f"{key} must be a decimal integer")
            numbers[key] = int(raw)
        result = await self._run(lambda: self.service.memory_list_page(
            namespace=query.get("namespace"), **numbers,
            sort_by=query.get("sort_by", "updated_at"),
            updated_after=query.get("updated_after"), updated_before=query.get("updated_before"),
            keywords=query.getlist("keywords"), categories=query.getlist("categories")))
        return json_response(result)

    @_maps_domain_errors
    async def handle_get_memory(self, request: Request):
        mid = request.path_params.get("memory_id")
        logger.info("GET /api/memories/%s", mid)
        result = await self._run(lambda: self.service.memory_get(mid))
        if result is None:
            return error_response(404, "Not found")
        return json_response(result)

    @_maps_domain_errors
    async def handle_get_details(self, request: Request):
        body = await request.json()
        ids = body.get("memory_ids", [])
        logger.info("POST /api/memories/details count=%d", len(ids))
        result = await self._run(lambda: self.service.memory_get_details(ids))
        return json_response(result)

    @_maps_domain_errors
    async def handle_add_memory(self, request: Request):
        body = await _request_object(request, required=("text",), string_fields=("text",))
        logger.info("POST /api/memories text_len=%d", len(body.get("text", "")))
        result = await self._run(lambda: self.service.memory_add(
            body["text"], body.get("metadata"), body.get("namespace"),
            body.get("summary"), body.get("confirm_codes")), write=True)
        return json_response(result, status=201)

    @_maps_domain_errors
    async def handle_update_memory(self, request: Request):
        mid = request.path_params.get("memory_id")
        body = await _request_object(request, required=(), string_fields=("text", "summary"))
        logger.info("PUT /api/memories/%s", mid)
        # An explicit blank string is the only way to clear the summary; null or
        # omitted keeps the existing derive/preserve policy.
        summary = body.get("summary")
        clear_summary = isinstance(summary, str) and not summary.strip()
        result = await self._run(lambda: self.service.memory_update(
            mid, body.get("text"), body.get("metadata"),
            body.get("namespace"), None if clear_summary else summary,
            body.get("confirm_codes"), clear_summary=clear_summary), write=True)
        if not result["success"]:
            return error_response(404, "Not found")
        return json_response(result)

    @_maps_domain_errors
    async def handle_delete_memory(self, request: Request):
        mid = request.path_params.get("memory_id")
        logger.info("DELETE /api/memories/%s", mid)
        result = await self._run(lambda: self.service.memory_delete(mid), write=True)
        if not result["success"]:
            return error_response(404, "Not found")
        return json_response(result)

    @_maps_domain_errors
    async def handle_clear_memories(self, request: Request):
        ns = request.query_params.get("namespace")
        logger.info("DELETE /api/memories namespace=%s", ns)
        result = await self._run(lambda: self.service.memory_clear(namespace=ns))
        return json_response(result)

    async def handle_import_memories(self, request: Request):
        temp_path = None
        started = time.perf_counter()
        original_name = "upload.jsonl"
        prefix = ""
        total = 0
        runtime = self.runtime

        def release_action(error):
            # The reservation owner releases; once Runtime has started the job its
            # completion waiter owns the action instead.
            if runtime is not None and not request.scope.get("_import_owner"):
                runtime.maintenance.finish_action(error=error)

        if runtime is not None:
            try:
                runtime.maintenance.start_action("import")
            except MaintenanceRejected as exc:
                return _maintenance_response(exc)
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
                # From here the runtime owns the action and the input file, even
                # if this HTTP request is cancelled.
                request.scope["_import_owner"] = "runtime" if runtime is not None else "service"
                if runtime is not None:
                    result = runtime.start_maintenance_import(
                        temp_path, original_name, prefix, reserved=True)
                else:
                    result = self.service.memory_import_start(temp_path, original_name, prefix)
                request.scope["_started_import_id"] = result["import_id"]
                return result
            result = await self._run(start_import)
            temp_path = None  # background job owns cleanup
            return Response(json.dumps(result), status_code=202, media_type="application/json")
        except asyncio.CancelledError as exc:
            if not request.scope.get("_started_import_id"):
                unlink_quietly(temp_path)
            release_action(exc)
            raise
        except MaintenanceRejected as exc:
            unlink_quietly(temp_path)
            release_action(exc)
            return _maintenance_response(exc)
        except RuntimeError as e:
            unlink_quietly(temp_path)
            release_action(e)
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
            release_action(e)
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
            release_action(e)
            logger.error("POST /api/import failed: %s", e, exc_info=True)
            self.audit.record(
                "memory_import",
                {"filename": os.path.basename(original_name), "bytes": total,
                 "namespace_prefix": prefix}, None,
                (time.perf_counter() - started) * 1000,
                error={"type": type(e).__name__, "message": str(e)},
            )
            return Response(json.dumps({"detail": "Internal error"}), status_code=500, media_type="application/json")

    @_maps_domain_errors
    async def handle_import_status(self, request: Request):
        if self.runtime is not None:
            result = self.runtime.maintenance_import_status(request.path_params["import_id"])
        else:
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

    @_maps_domain_errors
    async def handle_search_memories(self, request: Request):
        body = await _request_object(request, required=("query",), string_fields=("query",))
        include_resolution = body.get("include_resolution", False)
        if not isinstance(include_resolution, bool):
            raise InvalidRequest("include_resolution must be a boolean")
        logger.info("POST /api/memories/search query_len=%d", len(body.get("query", "")))
        results = await self._run(lambda: self.service.memory_search(
            body["query"], _number(body.get("k", 5), int, "k"),
            _number(body.get("threshold", 0.0), float, "threshold"), body.get("namespace"),
            keywords=body.get("keywords"),
            categories=body.get("categories"), return_resolution=include_resolution,
            updated_after=body.get("updated_after"), updated_before=body.get("updated_before")))
        return json_response(results)

    async def _terms_search(self, request, operation, default_k, label):
        """Shared mechanical flow for the lexical/anchor term searches.

        Parsing, namespace handling and the JSON envelope are shared; the
        operation's query implementation and scoring stay in the service. ``k``
        is passed through raw so the service/storage validate it strictly
        (bool/non-integer/out-of-range -> invalid_query) instead of truncating.
        """
        body = await _request_object(request, required=("query",), string_fields=("query",))
        logger.info("%s query_len=%d", label, len(body.get("query", "")))
        results = await self._run(lambda: operation(
            body["query"], body.get("k", default_k), body.get("namespace"),
            categories=body.get("categories"), keywords=body.get("keywords"),
            updated_after=body.get("updated_after"), updated_before=body.get("updated_before"),
            min_score=body.get("min_score")))
        return json_response(results)

    @_maps_domain_errors
    async def handle_search_lexical(self, request: Request):
        return await self._terms_search(request, self.service.memory_search_lexical, 10,
                                        "POST /api/memories/search/lexical")

    @_maps_domain_errors
    async def handle_search_anchor(self, request: Request):
        return await self._terms_search(request, self.service.memory_search_anchor, 10,
                                        "POST /api/memories/search/anchor")

    async def handle_keywords(self, request: Request):
        started = time.perf_counter()
        body, result, error = {}, None, None
        try:
            body = await request.json()
            allowed = {"namespace", "mode", "queries", "limit", "offset", "sort_by", "min_score",
                       "categories"}
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

    @_maps_domain_errors
    async def handle_stats(self, request: Request):
        logger.info("GET /api/stats")
        stats = await self._run(lambda: self.service.memory_stats())
        return json_response({**stats, "default_namespace": self.service.settings.DEFAULT_NAMESPACE})

    @_maps_domain_errors
    async def handle_sync(self, request: Request):
        logger.info("POST /api/sync")
        if self.runtime is not None:
            result = await self._run(lambda: self.runtime.maintenance_sync())
        else:
            result = await self._run(lambda: self.service.memory_sync())
        return json_response(result)

    @_maps_domain_errors
    async def handle_storage_paths(self, request: Request):
        if self.runtime is not None and self.service is None:
            s = self.runtime.settings
            result = {"backend": s.STORAGE_BACKEND}
            if s.STORAGE_BACKEND == "dual":
                relative = s.environment.get("MEMORY_PATHS_ACTIVE") == "1"
                result.update(primary_path=os.path.relpath(s.STORAGE_PATH, s.DATA_DIR) if relative else s.STORAGE_PATH,
                              secondary_path=os.path.relpath(s.REPLICA_PATH, s.DATA_DIR) if relative else s.REPLICA_PATH)
        else:
            result = await self._run(lambda: self.service.memory_storage_paths())
        return json_response(result)

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

    @_maps_domain_errors(domain=False, extras=((ValueError, lambda exc: error_response(400, str(exc))),))
    async def handle_migrate_namespace(self, request: Request):
        body = await request.json()
        source = body["source"]
        target = body["target"]
        logger.info("POST /api/namespaces/migrate source=%s target=%s", source, target)
        result = await self._run(lambda: self.service.memory_migrate_namespace(source, target))
        return json_response(result)

    @_maps_domain_errors
    async def handle_audit(self, request: Request):
        """Tool-call and background-work audit journal (newest first)."""
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
        return json_response(events)

    # Explicit access policy per route. ``boundary`` applies the shared domain
    # error mapping; ``none`` keeps the handler's own cleanup/audit handling.
    ROUTE_TABLE: tuple[_Route, ...] = (
        _Route("/admin/console", ("GET",), "handle_admin", "static", errors="none"),
        _Route("/admin/console/static/{asset:path}", ("GET",), "handle_asset", "asset", errors="none"),
        _Route("/api/memories", ("GET",), "handle_list_memories", "read"),
        _Route("/api/memories", ("POST",), "handle_add_memory", "write"),
        _Route("/api/memories", ("DELETE",), "handle_clear_memories", "action", action="clear"),
        _Route("/api/memories/page", ("GET",), "handle_list_memories_page", "read"),
        _Route("/api/memories/search", ("POST",), "handle_search_memories", "read"),
        _Route("/api/memories/search/lexical", ("POST",), "handle_search_lexical", "read"),
        _Route("/api/memories/search/anchor", ("POST",), "handle_search_anchor", "read"),
        _Route("/api/memories/details", ("POST",), "handle_get_details", "read"),
        _Route("/api/memories/{memory_id}", ("GET",), "handle_get_memory", "read"),
        _Route("/api/memories/{memory_id}", ("PUT",), "handle_update_memory", "write"),
        _Route("/api/memories/{memory_id}", ("DELETE",), "handle_delete_memory", "write"),
        _Route("/api/keywords/query", ("POST",), "handle_keywords", "read", errors="none"),
        _Route("/api/stats", ("GET",), "handle_stats", "read"),
        _Route("/api/storage-paths", ("GET",), "handle_storage_paths", "exempt"),
        _Route("/api/import", ("POST",), "handle_import_memories", "exempt", errors="none"),
        _Route("/api/import/{import_id}", ("GET",), "handle_import_status", "read"),
        _Route("/api/export/full", ("POST",), "handle_export_full", "action",
               action="export_full", errors="none"),
        _Route("/api/export/namespace", ("POST",), "handle_export_namespace", "action",
               action="export_namespace", errors="none"),
        _Route("/api/sync", ("POST",), "handle_sync", "action", action="sync"),
        _Route("/api/shutdown", ("POST",), "handle_shutdown", "action",
               action="shutdown", errors="none"),
        _Route("/api/restart", ("POST",), "handle_restart", "action",
               action="restart", errors="none"),
        _Route("/api/namespaces/migrate", ("POST",), "handle_migrate_namespace", "action",
               action="migrate_namespace"),
        _Route("/api/audit", ("GET",), "handle_audit", "exempt"),
        _Route("/api/maintenance", ("GET",), "handle_maintenance_status", "exempt", errors="none"),
        _Route("/api/model", ("GET",), "handle_model_status", "control", errors="none"),
        _Route("/api/model/retry", ("POST",), "handle_model_retry", "control", errors="none"),
        _Route("/api/config/model", ("GET", "POST"), "handle_model_config", "control", errors="none"),
        _Route("/api/config", ("GET", "POST"), "handle_model_config", "control", errors="none"),
        _Route("/api/maintenance/enter", ("POST",), "handle_maintenance_enter", "control", errors="none"),
        _Route("/api/maintenance/exit", ("POST",), "handle_maintenance_exit", "control", errors="none"),
        _Route("/api/recover", ("POST",), "handle_recover", "exempt", errors="none"),
        _Route("/api/recover/{recover_id}", ("GET",), "handle_recover_status", "exempt", errors="none"),
    )

    @property
    def routes(self):
        guarded = []
        for spec in self.ROUTE_TABLE:
            endpoint = getattr(self, spec.handler)
            if spec.policy == "action":
                endpoint = self._guard(endpoint, action=spec.action)
            elif spec.policy == "write":
                endpoint = self._guard(endpoint, defer=True)
            elif spec.policy == "read":
                endpoint = self._guard(endpoint)
            guarded.append(Route(spec.path, endpoint=endpoint, methods=list(spec.methods)))
        return guarded


def create_admin_routes(service=None, *, runtime=None, debug=False):
    return AdminAPI(service, runtime=runtime, debug=debug).routes
