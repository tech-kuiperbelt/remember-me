"""Phase 2: import coordination, Runtime-owned completion, release and snapshots."""
import threading
import time
import types
from pathlib import Path

import pytest

from app.import_coordinator import ImportCoordinator
from app.maintenance import MaintenanceRejected
from app.runtime import Runtime
from tests.conftest import FakeProvider
from tests.test_runtime import runtime, settings


def test_coordinator_wait_returns_final_status_and_cleans_file(tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text("body", encoding="utf-8")

    def job(import_id, file_path, original_name, prefix):
        coordinator.update(import_id, status="completed", phase="completed",
                           result={"imported": 1})

    coordinator = ImportCoordinator(job)
    started = coordinator.start(str(path), "in.jsonl")
    final = coordinator.wait(started["import_id"])
    assert final["status"] == "completed"
    assert final["result"] == {"imported": 1}
    assert not path.exists()  # cleanup complete before wait returns
    coordinator.close()


def test_coordinator_records_terminal_status_on_unexpected_exception(tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text("body", encoding="utf-8")

    def job(import_id, file_path, original_name, prefix):
        raise RuntimeError("boom")

    coordinator = ImportCoordinator(job)
    final = coordinator.wait(coordinator.start(str(path), "in.jsonl")["import_id"])
    assert final["status"] == "failed"
    assert final["error"]["type"] == "RuntimeError"
    assert not path.exists()
    assert coordinator._active_id is None
    coordinator.close()


def test_coordinator_thread_start_failure_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "in.jsonl"
    path.write_text("body", encoding="utf-8")
    coordinator = ImportCoordinator(lambda *args: None)

    def explode():
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(threading, "Thread",
                        lambda *a, **k: types.SimpleNamespace(start=explode))
    with pytest.raises(RuntimeError, match="cannot start thread"):
        coordinator.start(str(path), "in.jsonl")
    assert coordinator._active_id is None
    assert coordinator._jobs == {}
    assert path.exists()  # caller keeps ownership
    coordinator.close()


def test_start_maintenance_import_holds_action_until_wait_returns(tmp_path, monkeypatch):
    rt = runtime(tmp_path).start()
    rt.enter_maintenance()
    waited, release = threading.Event(), threading.Event()

    def start(*args):
        return {"import_id": "im_test", "status": "queued"}

    def wait(import_id, timeout=None):
        waited.set()
        assert release.wait(5)
        return {"import_id": import_id, "status": "completed"}

    monkeypatch.setattr(rt.service, "memory_import_start", start)
    monkeypatch.setattr(rt.service, "wait_import", wait)
    result = rt.start_maintenance_import("/tmp/in.jsonl", "in.jsonl")
    assert result["import_id"] == "im_test"
    try:
        assert waited.wait(5)
        assert rt.maintenance.snapshot()["action"] == "import"
        with pytest.raises(MaintenanceRejected):
            rt.exit_maintenance()
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while rt.maintenance.snapshot()["action"]:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert rt.maintenance.snapshot()["action"] is None
    rt.exit_maintenance()
    rt.close()


def test_start_maintenance_import_failure_releases_action(tmp_path, monkeypatch):
    rt = runtime(tmp_path).start()
    rt.enter_maintenance()

    def fail(*args):
        raise RuntimeError("import rejected")

    monkeypatch.setattr(rt.service, "memory_import_start", fail)
    with pytest.raises(RuntimeError, match="import rejected"):
        rt.start_maintenance_import("/tmp/in.jsonl", "in.jsonl")
    assert rt.maintenance.snapshot()["action"] is None
    rt.exit_maintenance()
    rt.close()


def test_maintenance_sync_keeps_marker_and_error_when_verification_fails(tmp_path, monkeypatch):
    rt = runtime(tmp_path).start()
    marker = Path(f"{rt.settings.STORAGE_PATH}.online-recover")
    marker.write_text("pending\n", encoding="utf-8")
    rt.maintenance.error = "recover failed"
    monkeypatch.setattr(rt, "_verify_recovered_replica",
                        lambda: (_ for _ in ()).throw(RuntimeError("drift")))
    rt.maintenance_sync()
    assert marker.exists()
    assert rt.maintenance.snapshot()["error"] == "recover failed"
    rt.close()


def test_maintenance_sync_clears_marker_and_error_after_verification(tmp_path):
    rt = runtime(tmp_path).start()
    marker = Path(f"{rt.settings.STORAGE_PATH}.online-recover")
    marker.write_text("pending\n", encoding="utf-8")
    rt.maintenance.error = "recover failed"
    rt.maintenance_sync()
    assert not marker.exists()
    assert rt.maintenance.snapshot()["error"] is None
    rt.close()


def test_recover_status_is_an_isolated_snapshot(tmp_path):
    rt = Runtime(settings(tmp_path), provider_factory=lambda **_: FakeProvider(),
                 configure_logging=False)
    rt._new_recover_job("rc_test", "backup.jsonl")
    snapshot = rt.recover_status("rc_test")
    assert snapshot["status"] == "queued"
    snapshot["status"] = "mutated"
    rt._update_recover_job("rc_test", status="running")
    assert rt.recover_status("rc_test")["status"] == "running"
    assert rt.recover_status("missing") is None


def test_close_business_continues_after_a_failing_closer(tmp_path, monkeypatch):
    rt = runtime(tmp_path).start()
    released = {"storage": False, "provider": False}

    def boom():
        raise RuntimeError("service close failed")

    monkeypatch.setattr(rt.service, "close", boom)
    monkeypatch.setattr(rt.storage, "stop", lambda: released.__setitem__("storage", True))
    monkeypatch.setattr(rt.provider, "close", lambda: released.__setitem__("provider", True))
    with pytest.raises(RuntimeError, match="service close failed"):
        rt._close_business()
    assert released == {"storage": True, "provider": True}
    assert rt.service is None and rt.storage is None and rt.provider is None
    rt.close()


def test_wait_timeout_does_not_report_completion_before_cleanup(tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text("body")
    terminal, release = threading.Event(), threading.Event()
    def job(import_id, *args):
        coordinator.update(import_id, status="completed")
        terminal.set()
        assert release.wait(5)
    coordinator = ImportCoordinator(job)
    import_id = coordinator.start(str(path), path.name)["import_id"]
    try:
        assert terminal.wait(5)
        with pytest.raises(TimeoutError, match="has not finished cleanup"):
            coordinator.wait(import_id, timeout=0)
        assert path.exists()
        assert coordinator.wait("unknown", timeout=0) is None
    finally:
        release.set()
        coordinator.close()
    assert coordinator.wait(import_id, timeout=0)["status"] == "completed"
    assert not path.exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_waiter_start_failure_drains_import_before_http_returns(tmp_path, monkeypatch, cancel):
    import asyncio
    from io import BytesIO
    from starlette.datastructures import UploadFile
    from app.admin import AdminAPI
    from tests.test_admin_cancellation import request, entered

    rt = runtime(tmp_path).start()
    rt.enter_maintenance()
    fallback, release = threading.Event(), threading.Event()
    inputs = []
    def job(import_id, file_path, *args):
        inputs.append(Path(file_path))
        assert release.wait(5)
        assert Path(file_path).exists()
        rt.service._set_import_job(import_id, status="completed", result={"imported": 1})
    monkeypatch.setattr(rt.service, "_run_import_job", job)
    original_wait = rt.service.wait_import
    def wait(import_id, timeout=None):
        fallback.set()
        return original_wait(import_id, timeout)
    monkeypatch.setattr(rt.service, "wait_import", wait)
    original_start = threading.Thread.start
    def start(thread):
        if thread.name.startswith("maintenance-import-"):
            raise RuntimeError("cannot start waiter")
        return original_start(thread)
    monkeypatch.setattr(threading.Thread, "start", start)

    async def scenario():
        req = request("POST")
        async def form():
            return {"file": UploadFile(BytesIO(b'{}\n'), filename="in.jsonl")}
        monkeypatch.setattr(req, "form", form)
        task = asyncio.create_task(AdminAPI(runtime=rt).handle_import_memories(req))
        try:
            await entered(fallback)
            assert not task.done()
            assert rt.maintenance.snapshot()["action"] == "import"
            with pytest.raises(MaintenanceRejected):
                rt.exit_maintenance()
            if cancel:
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert rt.maintenance.snapshot()["action"] == "import"
        finally:
            release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            response = await task
            assert response.status_code == 202
        assert inputs and all(not path.exists() for path in inputs)
        assert rt.maintenance.snapshot()["action"] is None
        rt.exit_maintenance()
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        rt.close()
