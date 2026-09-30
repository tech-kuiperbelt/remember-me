"""Audit trimming is declared per event, not inferred by a silent fallback."""

from app import audit

MCP_TOOLS = {
    "add", "update", "delete", "stats", "report",
    "search_semantic", "search_lexical", "search_anchor", "search_meta",
    "get_details", "list_keywords",
}


def test_mcp_tools_are_declared_audit_events():
    assert MCP_TOOLS <= audit.AUDIT_EVENTS


def test_every_declared_event_has_both_policies():
    assert audit._DEFAULT_POLICY in audit.PARAM_POLICIES
    assert audit._DEFAULT_POLICY in audit.RESULT_POLICIES
    for name in audit.AUDIT_EVENTS:
        assert name in audit.PARAM_POLICIES, name
        assert name in audit.RESULT_POLICIES, name


def test_unknown_event_still_records_with_default_policy():
    # Adding a tool must be an explicit act, but a stray event name must never
    # lose the event or raise.
    params = audit._trim_params("brand_new_tool", {"text": "secret", "summary": "s", "x": 1})
    assert "text" not in params and "summary" not in params and params["x"] == 1
    assert audit._trim_result("brand_new_tool", {"a": 1}) == {"a": 1}


def test_specialised_policies_are_applied():
    assert audit._trim_params("add", {"text": "t", "summary": "s"})["summary_mode"] == "input"
    duplicate = audit._trim_params("add", {"text": "t", "summary": "s"},
                                   result={"created": False})
    assert duplicate["summary_mode"] == "duplicate"
    transfer = audit._trim_params("memory_export", {"path": "/tmp/x", "type": "full"})
    assert "path" not in transfer
    assert "keyword_counts" not in audit._trim_result("stats", {"keyword_counts": {}})
