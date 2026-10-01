"""The tool table is the single source of truth for definitions and dispatch."""

import json
import re
from pathlib import Path

from app import audit
from app.transport.tools import (TOOL_SPECS, _TOOL_HINTS, _wrap_search_anchor,
                       _wrap_search_lexical, _wrap_search_meta, _wrap_search_semantic,
                       list_tool_definitions, tool_spec_for)

FIXTURES = Path(__file__).parent / "fixtures"
ALLOWED_NAMESPACE_MODES = {"search", "write", "keywords", "none"}


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_tool_definitions_match_golden():
    got = [tool.model_dump() for tool in list_tool_definitions()]
    assert got == _fixture("tool_definitions.json")


def test_tool_hints_match_golden():
    assert _TOOL_HINTS == _fixture("tool_hints.json")


def test_search_notes_match_golden():
    item = {"id": "m1", "summary": "s1", "score": 0.5}

    def without_id(wrapped):
        return {k: v for k, v in wrapped.items() if k != "search_id"}

    got = {
        "semantic_items": without_id(_wrap_search_semantic({"items": [item]})),
        "semantic_empty": without_id(_wrap_search_semantic({"items": []})),
        "lexical_items": without_id(_wrap_search_lexical([item])),
        "lexical_empty": without_id(_wrap_search_lexical([])),
        "anchor_items": without_id(_wrap_search_anchor([item])),
        "anchor_empty": without_id(_wrap_search_anchor([])),
        "meta_items": without_id(_wrap_search_meta([item], "ns", 10, 0, "created_at")),
        "meta_empty": without_id(_wrap_search_meta([], "ns", 10, 0, "created_at")),
        "meta_all_ns": without_id(_wrap_search_meta([item], None, 5, 0, "updated_at")),
    }
    assert got == _fixture("search_notes.json")


def test_every_spec_is_complete_and_policy_is_declared():
    names = [spec.name for spec in TOOL_SPECS]
    assert len(names) == 11 and len(set(names)) == 11
    for spec in TOOL_SPECS:
        assert callable(spec.handler)
        assert callable(spec.hint)
        assert spec.definition.name == spec.name
        assert spec.namespace_mode in ALLOWED_NAMESPACE_MODES
        assert spec.name in audit.AUDIT_EVENTS
        assert spec.name in audit.PARAM_POLICIES
        assert spec.name in audit.RESULT_POLICIES
        assert tool_spec_for(spec.name) is spec


def test_writes_and_search_tools_are_declared():
    writes = {spec.name for spec in TOOL_SPECS if spec.write}
    searches = {spec.name for spec in TOOL_SPECS if spec.search}
    assert writes == {"add", "update", "delete"}
    assert searches == {"search_semantic", "search_lexical", "search_anchor", "search_meta"}


def test_read_me_lists_exactly_the_registered_tools():
    text = (Path(__file__).parent.parent / "app" / "read_me.md").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "工具（11 个）" in l)
    listed = set(re.findall(r"`([a-z_]+)`", line))
    assert listed == {spec.name for spec in TOOL_SPECS}
