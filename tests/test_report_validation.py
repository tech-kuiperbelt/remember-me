"""MCP report feedback must belong to a recorded search result."""

import asyncio
import json

import pytest

from app.audit import AuditJournal, _trim_params
from app.report_feedback import SearchReceiptRegistry, InvalidReport
from app.server import create_mcp_server


def call(api, name, arguments):
    result = asyncio.run(api.handle_call_tool(name, arguments))
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("tool,arguments", [
    ("search_semantic", {"query": "ReceiptToken memory"}),
    ("search_lexical", {"query": "ReceiptToken"}),
    ("search_anchor", {"query": "ReceiptToken"}),
    ("search_meta", {"limit": 1}),
])
def test_report_validates_each_search_primitive(service, tool, arguments):
    api = create_mcp_server(service=service)
    call(api, "add", {"text": "ReceiptToken memory", "metadata": {"anchors": ["ReceiptToken"]}})
    search = call(api, tool, arguments)
    assert search["items"]
    report = call(api, "report", {
        "search_id": search["search_id"],
        "ratings": [{"m_id": search["items"][0]["id"], "rating": 4}],
        "feedback_source": "llm", "feedback_purpose": "task",
    })
    assert report["recorded"] is True and report["count"] == 1
    assert report["feedback_context"]["search_tool"] == tool
    assert report["feedback_context"]["result_membership"] == "validated"
    assert report["feedback_context"]["analysis_group"] == "declared_llm_task"
    assert report["feedback_context"]["declaration_trust"] == "caller_declared"


def test_unknown_and_foreign_result_ids_are_rejected_and_audited(tmp_path, service):
    journal = AuditJournal({"MEMORY_AUDIT_PATH": str(tmp_path / "audit.jsonl")})
    service.audit = journal
    api = create_mcp_server(service=service)
    added = call(api, "add", {"text": "First memory"})
    search = call(api, "search_meta", {"limit": 1})
    bad_id = call(api, "report", {
        "search_id": search["search_id"],
        "ratings": [{"m_id": "not-from-search", "rating": 5}],
        "feedback_source": "test", "feedback_purpose": "test",
    })
    assert bad_id["recorded"] is False
    assert bad_id["response_code"] == "invalid_report"
    assert bad_id["response_meta"]["reason"] == "result_id_mismatch"
    unknown = call(api, "report", {
        "search_id": "se_forged", "ratings": [{"m_id": added["id"], "rating": 5}]})
    assert unknown["response_meta"]["reason"] == "search_not_found"
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    invalid = [event for event in events if event["tool"] == "report"]
    assert len(invalid) == 2
    assert all(event["status"] == "error" and event["response_code"] == "invalid_report"
               for event in invalid)
    assert invalid[0]["params"]["feedback_source"] == "test"
    journal.close()


def test_valid_feedback_context_is_preserved_in_audit(tmp_path, service):
    journal = AuditJournal({"MEMORY_AUDIT_PATH": str(tmp_path / "audit.jsonl")})
    service.audit = journal
    api = create_mcp_server(service=service)
    item = call(api, "add", {"text": "Feedback audit memory"})
    search = call(api, "search_meta", {"limit": 1})
    report = call(api, "report", {
        "search_id": search["search_id"],
        "ratings": [{"m_id": item["id"], "rating": 3}],
        "feedback_source": "test", "feedback_purpose": "test",
    })
    assert report["recorded"] is True
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    event = next(event for event in events if event["tool"] == "report")
    assert event["status"] == "ok"
    assert event["search_id"] == search["search_id"]
    assert event["result"]["feedback_context"]["analysis_group"] == "declared_test"
    assert event["result"]["feedback_context"]["result_membership"] == "validated"
    assert event["params"]["feedback_source"] == "test"
    journal.close()


@pytest.mark.parametrize("ratings,reason", [
    ([], "invalid_ratings"),
    ([{"m_id": "x", "rating": 11}], "invalid_ratings"),
    ([{"m_id": "x", "rating": 1.5}], "invalid_ratings"),
    ([{"m_id": "x", "rating": True}], "invalid_ratings"),
    ([{"m_id": "x", "rating": 1}, {"m_id": "x", "rating": 2}], "duplicate_memory_id"),
])
def test_invalid_ratings_are_rejected(service, ratings, reason):
    api = create_mcp_server(service=service)
    item = call(api, "add", {"text": "First memory"})
    search = call(api, "search_meta", {})
    for rating in ratings:
        rating["m_id"] = item["id"]
    result = call(api, "report", {"search_id": search["search_id"], "ratings": ratings})
    assert result["recorded"] is False
    assert result["response_meta"]["reason"] == reason


def test_empty_search_and_old_process_search_cannot_be_reported(service):
    api = create_mcp_server(service=service)
    empty = call(api, "search_meta", {})
    assert empty["items"] == []
    rejected = call(api, "report", {"search_id": empty["search_id"],
                                    "ratings": [{"m_id": "any", "rating": 1}]})
    assert rejected["response_meta"]["reason"] == "search_not_found"
    item = call(api, "add", {"text": "First memory"})
    search = call(api, "search_meta", {})
    new_app = create_mcp_server(service=service)
    rejected = call(new_app, "report", {"search_id": search["search_id"],
                                        "ratings": [{"m_id": item["id"], "rating": 1}]})
    assert rejected["response_meta"]["reason"] == "search_not_found"


def test_receipts_expire_and_are_bounded():
    context = {"request_id": "r", "transport": "streamable_http"}
    registry = SearchReceiptRegistry(ttl=3600, max_receipts=1)
    registry.remember("se_first", "search_meta", [{"id": "a"}], None, context)
    registry.remember("se_second", "search_meta", [{"id": "b"}], None, context)
    with pytest.raises(InvalidReport, match="not found"):
        registry.validate("se_first", [{"m_id": "a", "rating": 1}])
    assert registry.validate("se_second", [{"m_id": "b", "rating": 1}]).tool == "search_meta"
    expired = SearchReceiptRegistry(ttl=0)
    expired.remember("se_expired", "search_meta", [{"id": "a"}], None, context)
    with pytest.raises(InvalidReport, match="expired"):
        expired.validate("se_expired", [{"m_id": "a", "rating": 1}])


def test_feedback_labels_are_declared_not_verified(service):
    api = create_mcp_server(service=service)
    item = call(api, "add", {"text": "First memory"})
    search = call(api, "search_meta", {})
    base = {"search_id": search["search_id"],
            "ratings": [{"m_id": item["id"], "rating": 1}]}
    unspecified = call(api, "report", base)
    assert unspecified["feedback_context"]["analysis_group"] == "unclassified"
    declared = call(api, "report", {**base, "feedback_source": "human",
                                    "feedback_purpose": "evaluation"})
    assert declared["feedback_context"]["analysis_group"] == "declared_human_evaluation"
    assert declared["feedback_context"]["declaration_trust"] == "caller_declared"
    invalid = call(api, "report", {**base, "feedback_source": "verified_human"})
    assert invalid["response_meta"]["reason"] == "invalid_source"


def test_audit_crops_untrusted_report_arguments():
    args = {"search_id": "s" * 200, "feedback_source": "x" * 200,
            "ratings": [{"m_id": "m" * 200, "rating": "r" * 200}] * 1001}
    trimmed = _trim_params("report", args)
    assert len(trimmed["search_id"]) == 64
    assert len(trimmed["feedback_source"]) == 64
    assert len(trimmed["ratings"]) == 1000
    assert len(trimmed["ratings"][0]["m_id"]) == 64
    assert len(trimmed["ratings"][0]["rating"]) == 32
    assert trimmed["ratings_total"] == 1001
