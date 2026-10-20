"""Real spawned preparation workers, without model weights or production data."""
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

from app.operations.model_preparation import ModelProgress, PreparationCancelled
from app.operations.model_process import prepare_model_in_process


def _complete(environment, progress, cancelled):
    progress.bytes(5, 10, "weights")
    Path(environment["marker"]).write_text(str(os.getpid()))


def _fail(environment, progress, cancelled):
    raise RuntimeError("failed through https://user:password@proxy.invalid:7890")


def _crash(environment, progress, cancelled):
    os._exit(7)


def _cooperative(environment, progress, cancelled):
    Path(environment["marker"]).write_text(str(os.getpid()))
    while not cancelled.is_set():
        time.sleep(.01)
    Path(environment["marker"] + ".cancelled").write_text("cancelled")


def _blocked(environment, progress, cancelled):
    if environment.get("ignore_term"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(environment["marker"]).write_text(str(os.getpid()))
    if environment.get("kind") == "retry":
        time.sleep(120)
    elif environment.get("kind") == "flood":
        while True:
            progress.bytes(5, 10, "weights")
    else:
        with socket.create_connection(("127.0.0.1", int(environment["port"]))) as stream:
            stream.settimeout(None)
            stream.sendall(b"HEAD /modules.json HTTP/1.0\r\n\r\n")
            if environment.get("kind") == "body":
                stream.recv(4)
            stream.recv(4096)


def _wait_marker(marker):
    deadline = time.monotonic() + 10
    while True:
        try:
            return int(marker.read_text())
        except (FileNotFoundError, ValueError):
            pass
        assert time.monotonic() < deadline, "preparation child did not start"
        time.sleep(.02)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Linux may briefly retain an orphan as a zombie until init reaps it.
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists() and stat.read_text().split()[2] == "Z":
        return False
    return True


def test_preparation_success_and_crash_are_reaped(tmp_path):
    prepare_model_in_process({"marker": str(tmp_path / "done")}, ModelProgress(),
                             threading.Event(), prepare=_complete)
    assert not _alive(int((tmp_path / "done").read_text()))
    with pytest.raises(RuntimeError, match="unexpectedly.*7"):
        prepare_model_in_process({}, ModelProgress(), threading.Event(), prepare=_crash)


def test_child_error_is_redacted():
    with pytest.raises(RuntimeError) as exc:
        prepare_model_in_process({}, ModelProgress(), threading.Event(), prepare=_fail)
    assert "password" not in str(exc.value)
    assert "RuntimeError" in str(exc.value)


def test_preparation_cancelled_before_start():
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(PreparationCancelled):
        prepare_model_in_process({}, ModelProgress(), cancelled, prepare=_crash)


def test_child_receives_cooperative_cancel(tmp_path):
    marker = tmp_path / "pid"
    cancelled = threading.Event()
    errors = []

    def run():
        try:
            prepare_model_in_process({"marker": str(marker)}, ModelProgress(), cancelled,
                                     prepare=_cooperative)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        pid = _wait_marker(marker)
        cancelled.set()
        worker.join(5)
        assert not worker.is_alive() and not _alive(pid)
        assert marker.with_suffix(".cancelled").read_text() == "cancelled"
        assert isinstance(errors[0], PreparationCancelled)
    finally:
        cancelled.set()
        worker.join(5)


@pytest.mark.parametrize("kind", ["headers", "body", "retry", "flood"])
def test_cancel_blocked_preparation_reaps_child(tmp_path, kind):
    marker = tmp_path / "pid"
    cancelled = threading.Event()
    errors = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        environment = {"marker": str(marker), "port": str(listener.getsockname()[1]),
                       "kind": kind}

        def run():
            try:
                prepare_model_in_process(environment, ModelProgress(), cancelled, prepare=_blocked)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        stream = None
        try:
            pid = _wait_marker(marker)
            if kind in ("headers", "body"):
                listener.settimeout(5)
                stream, _ = listener.accept()
                stream.recv(4096)
                if kind == "body":
                    stream.sendall(b"HTTP")
                time.sleep(.05)
            started = time.monotonic()
            cancelled.set()
            worker.join(5)
            assert not worker.is_alive()
            assert time.monotonic() - started < 5
            assert not _alive(pid)
            assert len(errors) == 1 and isinstance(errors[0], PreparationCancelled)
        finally:
            cancelled.set()
            if stream:
                stream.close()
            worker.join(5)


@pytest.mark.skipif(os.name == "nt", reason="Windows terminate is already forceful")
def test_cancel_escalates_to_kill(tmp_path):
    marker = tmp_path / "pid"
    cancelled = threading.Event()
    errors = []

    def run():
        try:
            prepare_model_in_process({"marker": str(marker), "kind": "retry", "ignore_term": "1"},
                                     ModelProgress(), cancelled, prepare=_blocked)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        pid = _wait_marker(marker)
        cancelled.set()
        worker.join(5)
        assert not worker.is_alive() and not _alive(pid)
        assert isinstance(errors[0], PreparationCancelled)
    finally:
        cancelled.set()
        worker.join(5)


def _parent(environment):
    prepare_model_in_process(environment, ModelProgress(), threading.Event(), prepare=_blocked)


@pytest.mark.parametrize("force", [False, True])
def test_child_exits_when_parent_is_terminated(tmp_path, force):
    marker = tmp_path / "pid"
    parent = multiprocessing.get_context("spawn").Process(
        target=_parent, args=({"marker": str(marker), "kind": "retry"},))
    parent.start()
    pid = None
    try:
        pid = _wait_marker(marker)
        if force:
            parent.kill()
        else:
            parent.terminate()
        parent.join(5)
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not _alive(pid)
    finally:
        if parent.is_alive():
            parent.kill()
            parent.join(5)
        parent.close()
        if pid and _alive(pid):
            os.kill(pid, signal.SIGTERM)


def test_runtime_loads_cache_only_after_child_exit(tmp_path, monkeypatch):
    from unittest.mock import patch
    import tempfile
    import app.runtime as runtime_module
    from app.config.settings import Settings
    from app.embedding.embed import ModelCacheMissing
    from tests.conftest import FakeProvider

    calls = []
    marker = tmp_path / "child.pid"

    def factory(**kwargs):
        calls.append(kwargs)
        assert kwargs.get("cache_only") is True
        assert "allow_download" not in kwargs
        if len(calls) == 1:
            raise ModelCacheMissing("missing")
        assert not _alive(int(marker.read_text()))
        return FakeProvider()

    def prepare(environment, progress, cancelled):
        prepare_model_in_process({"marker": str(marker)}, progress, cancelled, prepare=_complete)

    monkeypatch.setattr(runtime_module, "create_embedder", factory)
    monkeypatch.setattr(runtime_module, "prepare_model_in_process", prepare)
    settings = Settings.from_config(tmp_path, environment={
        "MEMORY_STORAGE": "sqlite", "EMBED_PRELOAD": "false"})
    old_tempdir = tempfile.tempdir
    try:
        with patch.dict(os.environ, dict(os.environ)):
            runtime = runtime_module.Runtime(settings, provider_factory=factory, configure_logging=False)
            try:
                runtime.start(background_model=True)
                deadline = time.monotonic() + 10
                while runtime.model_status()["status"] not in ("ready", "failed"):
                    assert time.monotonic() < deadline
                    time.sleep(.02)
                assert runtime.model_status()["status"] == "ready", runtime.model_status()
                assert len(calls) == 2
            finally:
                runtime.close()
    finally:
        tempfile.tempdir = old_tempdir


def _server_prepare(environment, progress, cancelled):
    root = Path(environment["MEMORY_DATA_DIR"])
    (root / "download.pid").write_text(str(os.getpid()))
    with socket.create_connection(("127.0.0.1", int(os.environ["MODEL_TEST_PORT"]))) as stream:
        stream.settimeout(None)
        stream.sendall(b"HEAD /modules.json HTTP/1.0\r\n\r\n")
        stream.recv(4096)


def _server_main():
    """Use real Uvicorn, Runtime and exec restart with an isolated fake model."""
    import asyncio
    from functools import partial
    import app.runtime as runtime_module
    import app.server as server_module
    from app.embedding.embed import ModelCacheMissing

    def missing(**kwargs):
        raise ModelCacheMissing("isolated test cache missing")

    runtime_module.create_embedder = missing
    runtime_module.prepare_model_in_process = partial(prepare_model_in_process,
                                                     prepare=_server_prepare)
    runtime_module.create_runtime = lambda settings: runtime_module.Runtime(
        settings, provider_factory=missing, configure_logging=False)
    original_exec = os.execv

    def restart(executable, arguments):
        original_exec(executable, [executable, "-m", "tests.test_model_process"] + arguments[3:])

    os.execv = restart
    asyncio.run(server_module.main())


@pytest.mark.parametrize("action", ["restart", "model/retry"])
def test_web_control_during_blocked_download_recovers_within_ten_seconds(tmp_path, action):
    import httpx

    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    with socket.socket() as download_server:
        download_server.bind(("127.0.0.1", 0))
        download_server.listen()
        download_server.settimeout(10)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("MEMORY_", "EMBED_", "FASTEMBED_",
                                      "SENTENCE_TRANSFORMERS_", "OLLAMA_"))}
        env.update(MODEL_TEST_PORT=str(download_server.getsockname()[1]),
                   EMBED_PROVIDER="sentence_transformers", MEMORY_STORAGE="sqlite")
        with (tmp_path / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-m", "tests.test_model_process",
                                       "--data-dir", str(tmp_path), "--port", str(port)],
                                      env=env, stdout=log, stderr=log)
            clients = []
            child_pid = None
            try:
                child_pid = _wait_marker(tmp_path / "download.pid")
                stream, _ = download_server.accept()
                clients.append(stream)
                stream.recv(4096)
                base = f"http://127.0.0.1:{port}"
                with httpx.Client(trust_env=False, timeout=1) as client:
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            if client.get(base + "/admin/console").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        assert time.monotonic() < deadline
                        time.sleep(.05)
                    started = time.monotonic()
                    response = client.post(base + "/api/" + action)
                    assert response.status_code == (200 if action == "restart" else 202)
                    if action == "restart":
                        assert response.json()["status"] == "restarting"
                    assert time.monotonic() - started < 1
                    while time.monotonic() - started < 10:
                        try:
                            new_pid = int((tmp_path / "download.pid").read_text())
                            if new_pid != child_pid and client.get(base + "/admin/console").status_code == 200:
                                break
                        except (httpx.HTTPError, ValueError):
                            pass
                        time.sleep(.05)
                    else:
                        pytest.fail((tmp_path / "server.log").read_text())
                    assert not _alive(child_pid)
                    elapsed = time.monotonic() - started
                    print(f"{action}: Web recovered in {elapsed:.3f}s")
                    assert client.get(base + "/api/model").json()["status"] == "downloading"
                    response = client.post(base + "/api/shutdown")
                    assert response.status_code == 200
                    server.wait(timeout=10)
                    assert not _alive(new_pid)
            finally:
                if server.poll() is None:
                    server.kill()
                    server.wait(timeout=5)
                for stream in clients:
                    stream.close()


if __name__ == "__main__":
    _server_main()
