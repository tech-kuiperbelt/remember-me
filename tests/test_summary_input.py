"""Caller-provided summary and asynchronous fallback safety tests."""

import asyncio
import json
import runpy
import sys
from unittest.mock import MagicMock

import pytest

from app import summary_worker


def test_guarded_summary_update_only_fills_matching_pending(storage, fake_emb):
    result = storage.add("original text", fake_emb, summary=None)
    mid = result["id"]

    assert storage.update_summary_if_pending(mid, "other text", "wrong") is False
    assert storage.get(mid).get("summary") is None
    assert storage.update_summary_if_pending(mid, "original text", "right") is True
    assert storage.get(mid)["summary"] == "right"
    assert storage.update_summary_if_pending(mid, "original text", "overwrite") is False
    assert storage.get(mid)["summary"] == "right"


@pytest.fixture
def worker(monkeypatch):
    storage = MagicMock()
    summarize = MagicMock(return_value="generated summary")
    audit_record = MagicMock()
    monkeypatch.setattr(summary_worker, "_storage", storage)
    monkeypatch.setattr(summary_worker, "summarize", summarize)
    monkeypatch.setattr(summary_worker.audit, "record", audit_record)
    return storage, summarize, audit_record


def test_worker_skips_task_when_summary_already_exists(worker):
    storage, summarize, audit_record = worker
    storage.get.return_value = {"text": "body", "summary": "caller summary"}

    assert summary_worker._process_task("id1", "body") is False
    summarize.assert_not_called()
    storage.update_summary_if_pending.assert_not_called()
    assert audit_record.call_args.args[2] == {"outcome": "stale_skipped"}
    assert audit_record.call_args.args[1]["reason"] == "summary_exists"


def test_worker_skips_task_for_changed_text(worker):
    storage, summarize, audit_record = worker
    storage.get.return_value = {"text": "new body", "summary": None}

    assert summary_worker._process_task("id1", "old body") is False
    summarize.assert_not_called()
    assert audit_record.call_args.args[1]["reason"] == "text_changed"


def test_worker_uses_guarded_write(worker):
    storage, summarize, audit_record = worker
    storage.get.return_value = {"text": "body", "summary": None}
    storage.update_summary_if_pending.return_value = True

    assert summary_worker._process_task("id1", "body") is True
    summarize.assert_called_once_with("body")
    storage.update_summary_if_pending.assert_called_once_with(
        "id1", "body", "generated summary"
    )
    assert audit_record.call_args.args[0] == "summary_fallback"
    assert audit_record.call_args.args[2] == {
        "outcome": "generated", "summary_length": 17
    }


def test_worker_does_not_overwrite_change_during_generation(worker):
    storage, _, audit_record = worker
    storage.get.return_value = {"text": "body", "summary": None}
    storage.update_summary_if_pending.return_value = False

    assert summary_worker._process_task("id1", "body") is False
    assert audit_record.call_args.args[1]["reason"] == "changed_during_generation"


def _load_server_tools():
    real_argv = sys.argv
    try:
        sys.argv = ["server"]
        namespace = runpy.run_module("app.server", run_name="not_main")
    finally:
        sys.argv = real_argv
    result = asyncio.run(namespace["handle_list_tools"]())
    return {tool.name: tool for tool in result.tools}


def test_mcp_write_tools_strongly_request_summary_but_keep_it_optional():
    tools = _load_server_tools()

    for name in ("add", "update"):
        tool = tools[name]
        assert "summary" in tool.inputSchema["properties"]
        assert "MUST" in tool.description
        assert "summary" not in tool.inputSchema["required"]


class _Request:
    def __init__(self, body, memory_id=None):
        self._body = body
        self.path_params = {"memory_id": memory_id} if memory_id else {}

    async def json(self):
        return self._body


def test_http_add_passes_summary(monkeypatch):
    from app import admin

    add = MagicMock(return_value={"id": "abc", "created": True})
    monkeypatch.setattr(admin, "memory_add", add)

    response = asyncio.run(admin.handle_add_memory(_Request({
        "text": "body", "metadata": {"keywords": ["k"]},
        "namespace": "ns", "summary": "short",
    })))

    assert response.status_code == 201
    assert json.loads(response.body) == {"id": "abc", "created": True}
    add.assert_called_once_with("body", {"keywords": ["k"]}, "ns", "short")


def test_http_update_passes_summary(monkeypatch):
    from app import admin

    update = MagicMock(return_value={"success": True})
    monkeypatch.setattr(admin, "memory_update", update)

    response = asyncio.run(admin.handle_update_memory(_Request(
        {"text": "new body", "summary": "new summary"}, memory_id="abc"
    )))

    assert response.status_code == 200
    assert json.loads(response.body) == {"success": True}
    update.assert_called_once_with("abc", "new body", None, None, "new summary")
