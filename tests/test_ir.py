"""The intermediate representation, which every adapter depends on."""

from __future__ import annotations

import pytest

from toolbaton import ir
from toolbaton.ir import Conversation, Message, ToolCall


def _conv(cid="c1", title="t", messages=None, **kw):
    return Conversation(id=cid, title=title, messages=messages or [], **kw)


def test_touches_matches_the_project_and_not_a_sibling_prefix(tmp_path):
    project = tmp_path / "repo"
    conv = _conv(files=[str(project / "src" / "a.py")])
    assert conv.touches(project)
    # "repo-two" must not match merely because it starts with "repo".
    assert not _conv(files=[str(tmp_path / "repo-two" / "a.py")]).touches(project)


def test_slug_and_date_are_filesystem_safe():
    conv = _conv(title="Fix: the /weird/ name!", created_at="2026-06-01T10:00:00Z")
    assert conv.slug == "fix-the-weird-name"
    assert conv.date == "2026-06-01"
    assert _conv(title="").slug == "untitled"


def test_merge_prefers_overlay_bodies_and_keeps_primary_metadata():
    primary = _conv("same", "Rich title", messages=[Message("1", "user", "old")],
                    plan="a plan", source="sqlite",
                    files=["/x/a.py"], created_at="2026-01-01T00:00:00Z")
    overlay = _conv("same", "worse title",
                    messages=[Message("1", "user", "new"),
                              Message("2", "assistant", "reply")],
                    source="transcript")
    merged = ir.merge_sources([primary], [overlay])
    assert len(merged) == 1
    assert merged[0].title == "Rich title"        # metadata from primary
    assert merged[0].plan == "a plan"
    assert len(merged[0].messages) == 2           # bodies from overlay
    assert "transcript" in merged[0].source and "sqlite" in merged[0].source


def test_merge_keeps_conversations_only_one_side_has():
    merged = ir.merge_sources([_conv("a")], [_conv("b")])
    assert {c.id for c in merged} == {"a", "b"}


def test_bundle_round_trips_including_tool_calls(tmp_path):
    conv = _conv(messages=[Message("1", "assistant", "hi", tools=[
        ToolCall(name="Read", source_name="read_file",
                 input={"path": "/x"}, result="ok", status="completed")])])
    path = ir.write_bundle([conv], tmp_path, "cursor", tmp_path)
    restored = ir.read_bundle(path)
    assert len(restored) == 1
    tool = restored[0].messages[0].tools[0]
    assert isinstance(tool, ToolCall)
    assert tool.source_name == "read_file"
    assert tool.result == "ok"


def test_bundle_rejects_an_unknown_schema():
    with pytest.raises(ValueError, match="schema"):
        ir.from_bundle({"schema": 999, "conversations": []})


def test_empty_message_detection():
    assert Message("1", "assistant").is_empty
    assert not Message("1", "assistant", text="x").is_empty
    assert not Message("1", "assistant", tools=[ToolCall(name="Read")]).is_empty
