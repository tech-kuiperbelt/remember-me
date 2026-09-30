"""MCP guidance must describe the result and suggest valid next calls."""

import asyncio
import json

import pytest

from app.server import create_mcp_server


def call(api, name, arguments):
    result = asyncio.run(api.handle_call_tool(name, arguments))
    return json.loads(result.content[0].text)


def test_tool_descriptions_match_search_feedback_and_detail_input(service):
    tools = {tool.name: tool for tool in asyncio.run(
        create_mcp_server(service=service).handle_list_tools()).tools}
    assert len(tools) == 11
    for name in ("search_semantic", "search_lexical", "search_anchor", "search_meta"):
        assert name in tools["report"].description
    assert tools["get_details"].inputSchema["properties"]["memory_ids"]["type"] == "array"
    assert "array" in tools["get_details"].description
    assert "force_sync" not in tools["delete"].description


@pytest.mark.parametrize("name,arguments,next_tool", [
    ("search_semantic", {"query": "missing"}, "search_lexical"),
    ("search_lexical", {"query": "MissingToken"}, "search_semantic"),
    ("search_anchor", {"query": "MissingAnchor"}, "search_lexical"),
    ("search_meta", {}, "search_semantic"),
])
def test_empty_search_suggests_retry_without_feedback(service, name, arguments, next_tool):
    result = call(create_mcp_server(service=service), name, arguments)
    assert result["items"] == []
    assert next_tool in result["hint"]
    assert "call report" not in result["note"]
    assert "call report" not in result["hint"]


def test_semantic_filter_miss_points_to_resolution(service, monkeypatch):
    monkeypatch.setattr(service.storage, "semantic_index_values", lambda *args: [])
    result = call(create_mcp_server(service=service), "search_semantic",
                  {"query": "missing", "categories": ["unlisted-category"]})
    assert result["items"] == []
    assert "filter_resolution" in result["hint"]
    assert "remove" in result["hint"]


def test_successful_search_links_details_and_report(service):
    api = create_mcp_server(service=service)
    call(api, "add", {"text": "DistinctiveToken memory", "metadata": {"anchors": ["DistinctiveToken"]}})
    for name, arguments in (
        ("search_semantic", {"query": "DistinctiveToken memory"}),
        ("search_lexical", {"query": "DistinctiveToken"}),
        ("search_anchor", {"query": "DistinctiveToken"}),
        ("search_meta", {}),
    ):
        result = call(api, name, arguments)
        assert result["items"]
        assert "source_search_id" in result["hint"]
        assert "call report" in result["note"]


def test_full_meta_page_points_to_next_offset(service):
    api = create_mcp_server(service=service)
    call(api, "add", {"text": "First memory"})
    call(api, "add", {"text": "Second memory"})
    first_page = call(api, "search_meta", {"limit": 1})
    assert "offset=1" in first_page["hint"]
    second_page = call(api, "search_meta", {"limit": 1, "offset": 1})
    assert first_page["items"][0]["id"] != second_page["items"][0]["id"]


def test_write_and_detail_hints_follow_actual_outcome(service):
    api = create_mcp_server(service=service)
    first = call(api, "add", {"text": "First memory"})
    duplicate = call(api, "add", {"text": "First memory"})
    assert duplicate["created"] is False
    assert "No new memory" in duplicate["hint"]
    assert "No memory was updated" in call(api, "update", {"memory_id": "missing"})["hint"]
    assert "No memory was deleted" in call(api, "delete", {"memory_id": "missing"})["hint"]
    details = call(api, "get_details", {"memory_ids": [first["id"], "missing"]})
    assert "Some IDs were missing" in details["hint"]
    missing = call(api, "get_details", {"memory_ids": ["missing"]})
    assert "None of the requested IDs" in missing["hint"]


def test_keyword_and_report_hints_follow_mode_and_count(service, monkeypatch):
    monkeypatch.setattr(service.storage, "semantic_index_values", lambda *args: [])
    api = create_mcp_server(service=service)
    browse = call(api, "list_keywords", {"mode": "browse"})
    semantic = call(api, "list_keywords", {"mode": "semantic", "queries": ["cache"]})
    assert "offset" in browse["hint"] and "min_score" not in browse["hint"]
    assert "min_score" in semantic["hint"] and "offset" not in semantic["hint"]
    empty_report = call(api, "report", {"search_id": "se_example", "ratings": []})
    assert empty_report["count"] == 0
    assert empty_report["response_code"] == "invalid_report"


def test_invalid_anchors_hint_does_not_claim_omission_always_preserves(service):
    result = call(create_mcp_server(service=service), "add",
                  {"text": "First memory", "metadata": {"anchors": [""]}})
    assert result["response_code"] == "invalid_anchors"
    assert "substantive text change clears" in result["hint"]
