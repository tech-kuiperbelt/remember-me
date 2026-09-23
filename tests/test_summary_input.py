"""Caller summaries and deterministic fallback behavior."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.embed import EmbeddingInputTooLong
from app.summary_policy import derive_summary, normalize_summary


def test_derive_summary_prefers_complete_first_sentence():
    assert derive_summary("  # 决策：停止摘要模型。后续仍有正文。\n第二段") == "决策：停止摘要模型。"


def test_derive_summary_joins_short_heading_and_next_line():
    assert derive_summary("总结：\n  这是具体的结论。\n其他内容") == "总结： 这是具体的结论。"


def test_derive_summary_collapses_whitespace_and_truncates_unicode():
    result = derive_summary("  " + "记 忆\t" * 40)
    assert len(result.removesuffix("…")) <= 80
    assert result.endswith("…")
    assert "  " not in result


def test_normalize_summary_treats_blank_as_missing():
    assert normalize_summary("  caller\n summary  ") == "caller\n summary"
    assert normalize_summary(" \n ") is None
    assert normalize_summary(123) is None


def test_storage_add_derives_summary_and_duplicate_is_untouched(storage, fake_emb):
    first = storage.add("决定：采用确定性摘要。更多内容。", fake_emb)
    assert storage.get(first["id"])["summary"] == "决定：采用确定性摘要。"
    duplicate = storage.add("决定：采用确定性摘要。更多内容。", fake_emb,
                            summary="replacement")
    assert duplicate == {"id": first["id"], "created": False}
    assert storage.get(first["id"])["summary"] == "决定：采用确定性摘要。"


def test_storage_update_summary_policy(storage, fake_emb):
    result = storage.add("old body", fake_emb, summary="original summary")
    mid = result["id"]
    storage.update(mid, metadata={"x": 1})
    assert storage.get(mid)["summary"] == "original summary"
    storage.update(mid, text=" old   body ", embedding=fake_emb)
    assert storage.get(mid)["summary"] == "original summary"
    storage.update(mid, text="新正文第一句。第二句。", embedding=fake_emb)
    assert storage.get(mid)["summary"] == "新正文第一句。"
    storage.update(mid, summary="  corrected\nsummary ")
    assert storage.get(mid)["summary"] == "corrected\nsummary"


def test_recovery_insert_preserves_null_summary(storage, fake_emb):
    storage._add_with_id("legacy", "历史正文第一句。第二句。", fake_emb, {}, "ns",
                         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")
    assert storage.get("legacy")["summary"] is None
    from app.service import _prepare_for_llm
    shaped = _prepare_for_llm([storage.get("legacy")])
    assert shaped[0]["summary"] == "历史正文第一句。"
    assert "text" not in shaped[0]


def _load_server_tools():
    from tests.conftest import server_namespace
    result = asyncio.run(server_namespace()["handle_list_tools"]())
    return {tool.name: tool for tool in result.tools}


def test_mcp_write_tools_request_summary_and_describe_deterministic_fallback():
    tools = _load_server_tools()
    for name in ("add", "update"):
        tool = tools[name]
        assert "summary" in tool.inputSchema["properties"]
        assert "MUST" in tool.description
        assert "deterministic" in tool.description
        assert "summary" not in tool.inputSchema["required"]


class _Request:
    def __init__(self, body, memory_id=None):
        self._body = body
        self.path_params = {"memory_id": memory_id} if memory_id else {}

    async def json(self):
        return self._body


def test_http_add_passes_summary(monkeypatch, admin):
    add = MagicMock(return_value={"id": "abc", "created": True})
    monkeypatch.setattr(admin.service, "memory_add", add)
    response = asyncio.run(admin.handle_add_memory(_Request({
        "text": "body", "metadata": {"keywords": ["k"]},
        "namespace": "ns", "summary": "short",
    })))
    assert response.status_code == 201
    assert json.loads(response.body) == {"id": "abc", "created": True}
    add.assert_called_once_with("body", {"keywords": ["k"]}, "ns", "short", None)


def test_http_update_passes_summary(monkeypatch, admin):
    update = MagicMock(return_value={"success": True})
    monkeypatch.setattr(admin.service, "memory_update", update)
    response = asyncio.run(admin.handle_update_memory(_Request(
        {"text": "new body", "summary": "new summary"}, memory_id="abc"
    )))
    assert response.status_code == 200
    assert json.loads(response.body) == {"success": True}
    update.assert_called_once_with("abc", "new body", None, None, "new summary", None)


@pytest.mark.parametrize("handler,body", [
    ("handle_add_memory", {"text": "long"}),
    ("handle_update_memory", {"text": "long"}),
    ("handle_search_memories", {"query": "long"}),
])
def test_http_embedding_input_too_long_returns_413(monkeypatch, handler, body, admin):
    target = {
        "handle_add_memory": "memory_add",
        "handle_update_memory": "memory_update",
        "handle_search_memories": "memory_search",
    }[handler]
    monkeypatch.setattr(admin.service, target,
                        MagicMock(side_effect=EmbeddingInputTooLong(9000, 8000)))
    request = _Request(body, memory_id="abc" if handler == "handle_update_memory" else None)
    response = asyncio.run(getattr(admin, handler)(request))
    assert response.status_code == 413
    assert json.loads(response.body)["code"] == "embedding_input_too_long"


def test_online_summary_generation_route_and_ui_are_removed(admin):
    admin_routes = admin.routes
    assert all(route.path != "/api/summarize" for route in admin_routes)
    ui = Path("web/index.html").read_text(encoding="utf-8")
    assert "doSummarize" not in ui
    assert "btn-summarize" not in ui
    assert "summary_fallback" not in ui


def test_online_recover_route_and_ui_are_removed(admin):
    admin_routes = admin.routes
    assert all(route.path != "/api/recover" for route in admin_routes)
    ui = Path("web/index.html").read_text(encoding="utf-8")
    assert "doRecover" not in ui
    assert "btn-recover" not in ui
