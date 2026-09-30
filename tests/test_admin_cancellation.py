"""Real admin handlers retain ownership while cancelled blocking work drains."""
import asyncio
import json
import threading
from pathlib import Path

import pytest
from starlette.requests import Request

from app.admin import AdminAPI
from app.calls import AsyncCallAdapter, ServiceClosing
from app.maintenance import MaintenanceRejected
from tests.test_runtime import runtime


def request(method="GET", body=None):
    async def receive():
        return {"type": "http.request", "body": json.dumps(body or {}).encode()}
    return Request({"type": "http", "method": method, "path": "/",
                    "headers": [], "query_string": b"", "path_params": {"memory_id": "missing"},
                    "state": {"audit_request_id": "cancel-review"}}, receive)


async def entered(event):
    async def wait():
        while not event.is_set():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 5)


def test_cancelled_admin_read_keeps_reader_until_worker_finishes(tmp_path, monkeypatch):
    rt = runtime(tmp_path).start()
    started, release = threading.Event(), threading.Event()
    finished = threading.Event()
    def read(_):
        started.set()
        assert release.wait(5)
        finished.set()
        return None
    monkeypatch.setattr(rt.service, "memory_get", read)
    async def scenario():
        api = AdminAPI(runtime=rt)
        task = asyncio.create_task(api._guard(api.handle_get_memory)(request()))
        try:
            await entered(started)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # repeated cancellation must not release the slot
            await asyncio.sleep(0)
            assert not finished.is_set()
            assert rt.maintenance.snapshot()["readers"] == 1
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert finished.is_set()
        assert rt.maintenance.snapshot()["readers"] == 0
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        rt.close()


@pytest.mark.parametrize("action,handler,operation", [
    ("clear", "handle_clear_memories", "memory_clear"),
    ("sync", "handle_sync", "memory_sync"),
])
def test_cancelled_admin_action_cannot_exit_maintenance(tmp_path, monkeypatch, action, handler, operation):
    rt = runtime(tmp_path).start()
    rt.enter_maintenance()
    started, release = threading.Event(), threading.Event()
    def work(**kwargs):
        started.set()
        assert release.wait(5)
        return {"success": True}
    monkeypatch.setattr(rt.service, operation, work)
    async def scenario():
        api = AdminAPI(runtime=rt)
        task = asyncio.create_task(api._guard(getattr(api, handler), action=action)(request("POST")))
        try:
            await entered(started)
            task.cancel()
            await asyncio.sleep(0)
            assert rt.maintenance.snapshot()["action"] == action
            with pytest.raises(MaintenanceRejected):
                rt.exit_maintenance()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert rt.maintenance.snapshot()["action"] is None
        rt.exit_maintenance()
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        rt.close()


@pytest.mark.parametrize("kind", ["full", "namespace"])
@pytest.mark.parametrize("failure", [False, True])
def test_cancelled_export_cleans_file_and_audits_once(tmp_path, monkeypatch, kind, failure):
    rt = runtime(tmp_path).start()
    rt.enter_maintenance()
    started, release = threading.Event(), threading.Event()
    def export(*args):
        path = args[-1]
        started.set()
        assert release.wait(5)
        Path(path).write_text("exported memory")
        if failure:
            raise RuntimeError("export failed")
        return {"path": path, "records": 1, "sha256": "test"}
    monkeypatch.setattr(rt.service, "memory_export_" + kind, export)
    async def scenario():
        api = AdminAPI(runtime=rt)
        task = asyncio.create_task(api._guard(getattr(api, "handle_export_" + kind),
                                             action="export_" + kind)(request("POST", {"namespace": "default"})))
        try:
            await entered(started)
            task.cancel()
            await asyncio.sleep(0)
            assert rt.maintenance.snapshot()["action"] == "export_" + kind
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not list(Path(rt.settings.TEMP_DIR).glob("remember-me-export-*.jsonl"))
        events = rt.audit.read_events(tool="memory_export")
        assert len(events) == 1
        assert events[0]["context"]["request_id"] == "cancel-review"
        assert bool(events[0].get("error")) == failure
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        rt.close()


def test_shutdown_immediately_cancels_both_queues_and_rejects_new_work(tmp_path):
    rt = runtime(tmp_path)
    rt.calls = AsyncCallAdapter(business_workers=1)
    rt.start()
    release = threading.Event()
    business, control = threading.Event(), threading.Event()
    ran = []
    def blocked(started):
        started.set()
        assert release.wait(5)
    async def scenario():
        first = asyncio.create_task(rt.calls.run(lambda: blocked(business)))
        second = asyncio.create_task(rt.calls.run(lambda: blocked(control), control=True))
        pending = []
        try:
            await entered(business)
            await entered(control)
            api = AdminAPI(runtime=rt)
            pending = [asyncio.create_task(api._run(lambda: ran.append("business"), write=True)),
                       asyncio.create_task(api._run(lambda: ran.append("control"), control=True))]
            await asyncio.sleep(0)
            rt.request_shutdown()
            assert rt.state == "closing"
            with pytest.raises(ServiceClosing):
                await api._run(lambda: ran.append("new"))
            for task in pending:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            assert not first.done() and not second.done()
        finally:
            release.set()
            await asyncio.gather(first, second)
        assert ran == []
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        rt.close()


def test_cancelled_queued_export_cleans_file_without_running(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    rt.calls = AsyncCallAdapter(business_workers=1)
    rt.start()
    rt.enter_maintenance()
    started, release = threading.Event(), threading.Event()
    ran = []
    def blocked():
        started.set()
        assert release.wait(5)
    monkeypatch.setattr(rt.service, "memory_export_full", lambda path: ran.append(path))
    async def scenario():
        blocker = asyncio.create_task(rt.calls.run(blocked))
        await entered(started)
        api = AdminAPI(runtime=rt)
        task = asyncio.create_task(api._guard(api.handle_export_full, action="export_full")(request("POST")))
        try:
            await asyncio.sleep(0)
            assert list(Path(rt.settings.TEMP_DIR).glob("remember-me-export-*.jsonl"))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not list(Path(rt.settings.TEMP_DIR).glob("remember-me-export-*.jsonl"))
            assert rt.maintenance.snapshot()["action"] is None
            assert not ran
        finally:
            release.set()
            await blocker
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        rt.close()


def test_cancelled_import_start_transfers_action_to_background_job(tmp_path, monkeypatch):
    from io import BytesIO
    from starlette.datastructures import UploadFile
    rt = runtime(tmp_path).start()
    rt.enter_maintenance()
    starting, start_release = threading.Event(), threading.Event()
    job_release = threading.Event()
    original = rt.service.memory_import_start
    def run_job(import_id, file_path, *args):
        try:
            assert job_release.wait(5)
        finally:
            Path(file_path).unlink(missing_ok=True)
            rt.service._set_import_job(import_id, status="completed")
    monkeypatch.setattr(rt.service, "_run_import_job", run_job)
    def start(*args):
        result = original(*args)
        starting.set()
        assert start_release.wait(5)
        return result
    monkeypatch.setattr(rt.service, "memory_import_start", start)
    async def scenario():
        req = request("POST")
        async def form():
            return {"file": UploadFile(BytesIO(b'{}\n'), filename="input.jsonl")}
        monkeypatch.setattr(req, "form", form)
        api = AdminAPI(runtime=rt)
        task = asyncio.create_task(api.handle_import_memories(req))
        try:
            await entered(starting)
            task.cancel()
            start_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert rt.maintenance.snapshot()["action"] == "import"
            with pytest.raises(MaintenanceRejected):
                rt.exit_maintenance()
            job_release.set()
            async def finished():
                while rt.maintenance.snapshot()["action"]:
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(finished(), 5)
            assert not list(Path(rt.settings.TEMP_DIR).glob("remember-me-import-*.jsonl"))
        finally:
            start_release.set()
            job_release.set()
    try:
        asyncio.run(scenario())
    finally:
        start_release.set()
        job_release.set()
        rt.close()
