"""Writing Claude Code sessions.

Two things break a written session in ways that are invisible until you try to
resume it, so both are pinned here: the filename/sessionId agreement, and the
tool_use / tool_result pairing the API enforces.
"""

from __future__ import annotations

import json

from toolbaton.ir import Conversation, Message, ToolCall
from toolbaton.platforms.base import WriteOptions
from toolbaton.platforms.claude_code import writer


def _conv():
    return Conversation(
        id="cccccccc-1111-4111-8111-cccccccccccc",
        title="A migrated thread",
        created_at="2026-06-01T10:00:00.000Z",
        updated_at="2026-06-02T10:00:00.000Z",
        source="cursor-sqlite", mode="agent",
        files=["/tmp/project/src/app.py"],
        messages=[
            Message("1", "user", "Do the thing"),
            Message("2", "assistant", "Working on it", thinking="a thought",
                    tools=[ToolCall(name="Read", source_name="read_file",
                                    input={"path": "/tmp/project/src/app.py"},
                                    result=None)]),
            Message("3", "assistant", "Done"),
        ])


def _write(tmp_path, **kw):
    options = WriteOptions(project=tmp_path / "project", out_dir=tmp_path / "out",
                           target_version="9.9.9", **kw)
    result = writer.write([_conv()], options)
    path = next(p for p in result.files if p.suffix == ".jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()], path


def test_filename_matches_the_session_id(tmp_path):
    records, path = _write(tmp_path)
    session_ids = {r["sessionId"] for r in records if "sessionId" in r}
    assert session_ids == {path.stem}


def test_every_line_is_valid_json_and_a_summary_leads(tmp_path):
    records, _ = _write(tmp_path)
    assert records[0]["type"] == "summary"
    assert records[0]["summary"].startswith("[baton] ")


def test_summary_leaf_points_at_the_last_record(tmp_path):
    records, _ = _write(tmp_path)
    assert records[0]["leafUuid"] == records[-1]["uuid"]


def test_parent_chain_is_contiguous(tmp_path):
    records, _ = _write(tmp_path)
    turns = [r for r in records if r.get("type") in ("user", "assistant")]
    assert turns[0]["parentUuid"] is None
    for previous, current in zip(turns, turns[1:]):
        assert current["parentUuid"] == previous["uuid"]


def test_first_turn_leads_with_the_title_not_a_banner(tmp_path):
    # The resume picker renders the first user message, so a banner here would
    # become the session's displayed name.
    records, _ = _write(tmp_path)
    first = next(r for r in records if r.get("type") == "user")
    text = first["message"]["content"][0]["text"]
    assert text.startswith("[baton] A migrated thread")
    assert "<!--" not in text


def test_blocks_mode_pairs_every_tool_use_with_a_result(tmp_path):
    # An unmatched tool_use makes the API reject the conversation on resume.
    records, _ = _write(tmp_path, tools="blocks")
    used, resulted = set(), set()
    for record in records:
        for block in (record.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                used.add(block["id"])
            elif block.get("type") == "tool_result":
                resulted.add(block["tool_use_id"])
    assert used and used == resulted


def test_text_mode_emits_no_tool_blocks_at_all(tmp_path):
    records, _ = _write(tmp_path, tools="text")
    kinds = {block.get("type")
             for record in records
             for block in (record.get("message") or {}).get("content") or []
             if isinstance(block, dict)}
    assert kinds == {"text"}


def test_drop_mode_omits_tool_content(tmp_path):
    records, _ = _write(tmp_path, tools="drop")
    body = json.dumps(records)
    assert "read_file" not in body


def test_uuids_are_deterministic_across_runs(tmp_path):
    first, _ = _write(tmp_path / "a")
    second, _ = _write(tmp_path / "b")
    assert [r.get("uuid") for r in first] == [r.get("uuid") for r in second]


def test_version_is_not_hardcoded(tmp_path):
    records, _ = _write(tmp_path)
    versions = {r["version"] for r in records if "version" in r}
    assert versions == {"9.9.9"}


def test_modes_change_only_the_preamble_shape(tmp_path):
    def first_text(records):
        record = next(r for r in records if r.get("type") == "user")
        return record["message"]["content"][0]["text"]

    minimal, _ = _write(tmp_path / "m", mode="minimal")
    assert first_text(minimal) == "[baton] A migrated thread"

    compact, _ = _write(tmp_path / "c", mode="compact")
    assert first_text(compact).startswith(
        "[baton] A migrated thread\n\nsource:cccccccc")
