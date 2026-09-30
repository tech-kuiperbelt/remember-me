"""Phase 1 regressions: bounded async bridge, admission re-check, cancellation,
provider serialization and shutdown ordering.
"""
import asyncio
import threading

import pytest

from app.calls import AsyncCallAdapter
from app.maintenance import MaintenanceGate, MaintenanceRejected
from app.provider_adapter import wrap_provider
from tests.conftest import FakeProvider
from tests.test_runtime import runtime


class _GatedProvider:
    """Provider that gates document embeddings until the test releases them."""

    def __init__(self):
        self.spec = FakeProvider().spec
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed_document(self, text):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        self.entered.set()
        assert self.release.wait(5)
        with self.lock:
            self.active -= 1
        return [0.1, 0.2, 0.3]

    def embed_query(self, text):
        return self.embed_document(text)

    def embed_documents(self, texts):
        return [self.embed_document(text) for text in texts]

    def warmup(self):
        pass


def test_control_call_runs_while_business_is_blocked():
    """A saturated business pool must not starve the control pool."""

    async def scenario():
        adapter = AsyncCallAdapter()
        adapter.enable()
        entered, release = threading.Event(), threading.Event()

        def slow():
            entered.set()
            assert release.wait(5)
            return "slow"

        slow_task = asyncio.create_task(adapter.run(slow))
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, entered.wait, 5)
        try:
            control = await asyncio.wait_for(
                adapter.run(lambda: "control", control=True), timeout=5)
            assert control == "control"
            assert not slow_task.done()
        finally:
            release.set()
        assert await slow_task == "slow"
        adapter.close()

    asyncio.run(scenario())


def test_business_pool_bounds_concurrency():
    async def scenario():
        adapter = AsyncCallAdapter(business_workers=4)
        adapter.enable()
        lock = threading.Lock()
        state = {"active": 0, "peak": 0}
        all_started, release = threading.Event(), threading.Event()

        def slow():
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                if state["active"] == 4:
                    all_started.set()
            assert release.wait(5)
            with lock:
                state["active"] -= 1

        tasks = [asyncio.create_task(adapter.run(slow)) for _ in range(6)]
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, all_started.wait, 5)
        with lock:
            assert state["active"] == 4 and state["peak"] == 4
        release.set()
        await asyncio.gather(*tasks)
        adapter.close()

    asyncio.run(scenario())


def test_queued_write_is_rechecked_after_maintenance_enters():
    """A write queued before maintenance must be re-checked when it runs."""

    async def scenario():
        gate = MaintenanceGate()
        adapter = AsyncCallAdapter(business_workers=1)
        adapter.enable()
        entered, release = threading.Event(), threading.Event()

        def slow_read():
            entered.set()
            assert release.wait(5)
            return "read"

        read_task = asyncio.create_task(
            adapter.run(slow_read, admission=gate.access()))
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, entered.wait, 5)
        write_task = asyncio.create_task(
            adapter.run(lambda: "write", admission=gate.access(write=True)))
        # The queued write holds no writer slot, so maintenance can enter.
        gate.enter()
        release.set()
        assert await read_task == "read"
        with pytest.raises(MaintenanceRejected) as excinfo:
            await write_task
        assert excinfo.value.code == "maintenance_mode"
        assert gate.snapshot()["writers"] == 0
        gate.exit()
        adapter.close()

    asyncio.run(scenario())


def test_cancellation_stops_queued_work_but_not_running_work():
    async def scenario():
        gate = MaintenanceGate()
        adapter = AsyncCallAdapter(business_workers=1)
        adapter.enable()
        entered, release = threading.Event(), threading.Event()
        ran = []

        def running():
            entered.set()
            assert release.wait(5)
            ran.append("running")
            return "running"

        def queued():
            ran.append("queued")
            return "queued"

        first = asyncio.create_task(adapter.run(running, admission=gate.access()))
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, entered.wait, 5)
        second = asyncio.create_task(
            adapter.run(queued, admission=gate.access(write=True)))
        await asyncio.sleep(0)  # let the second task submit and queue
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        release.set()
        assert await first == "running"
        assert ran == ["running"]  # queued work never ran
        assert gate.snapshot()["writers"] == 0
        adapter.close()

    asyncio.run(scenario())


def test_cancellation_does_not_stop_running_work():
    async def scenario():
        gate = MaintenanceGate()
        adapter = AsyncCallAdapter(business_workers=2)
        adapter.enable()
        first_entered, first_release = threading.Event(), threading.Event()
        second_entered, second_release = threading.Event(), threading.Event()
        finished = threading.Event()
        ran = []

        def slow_one():
            first_entered.set()
            assert first_release.wait(5)
            ran.append(1)
            return 1

        def slow_two():
            second_entered.set()
            assert second_release.wait(5)
            ran.append(2)
            finished.set()
            return 2

        first = asyncio.create_task(adapter.run(slow_one, admission=gate.access()))
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, first_entered.wait, 5)
        second = asyncio.create_task(adapter.run(slow_two, admission=gate.access()))
        assert await loop.run_in_executor(None, second_entered.wait, 5)
        second.cancel()
        second_release.set()  # the running work is allowed to finish
        first_release.set()
        assert await first == 1
        with pytest.raises(asyncio.CancelledError):
            await second
        assert await loop.run_in_executor(None, finished.wait, 5)
        assert sorted(ran) == [1, 2]  # cancelled running work still completed
        assert gate.snapshot()["writers"] == 0 and gate.snapshot()["readers"] == 0
        adapter.close()

    asyncio.run(scenario())


def test_serialized_provider_serializes_calls():
    provider = _GatedProvider()
    wrapped = wrap_provider(provider)
    results = []
    # Holding the adapter lock keeps both callers out of the provider. If the
    # wrapper did not hold its lock around the call, both would enter at once.
    wrapped._lock.acquire()
    try:
        threads = [
            threading.Thread(target=lambda i=i: results.append(wrapped.embed_document(str(i))))
            for i in range(2)
        ]
        for thread in threads:
            thread.start()
        assert provider.active == 0
    finally:
        wrapped._lock.release()
    assert provider.entered.wait(5)
    # One caller is inside and gated; the lock keeps the other out.
    assert provider.active == 1
    provider.release.set()
    for thread in threads:
        thread.join(5)
    assert provider.peak == 1
    assert len(results) == 2


def test_close_waits_for_running_adapter_work(tmp_path, monkeypatch):
    rt = runtime(tmp_path).start()
    entered, release = threading.Event(), threading.Event()

    def slow_embed(text):
        entered.set()
        assert release.wait(5)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(rt.service, "embed", slow_embed)
    outcome = {}

    def run_add():
        async def go():
            return await rt.calls.run(
                lambda: rt.service.memory_add("slow write"),
                admission=rt.maintenance.access(write=True))
        outcome["result"] = asyncio.run(go())

    worker = threading.Thread(target=run_add)
    worker.start()
    assert entered.wait(5)
    closed = threading.Event()
    closer = threading.Thread(target=lambda: (rt.close(), closed.set()))
    closer.start()
    try:
        # close() must wait for the running call instead of releasing storage.
        assert not closed.wait(0.2)
    finally:
        release.set()
        closer.join(10)
        worker.join(10)
    assert closed.is_set()
    assert outcome["result"]["id"]
    assert rt.state == "closed"
