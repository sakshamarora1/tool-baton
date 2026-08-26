"""Cursor -> IR -> Claude Code, and back again.

The end-to-end path both directions, on synthesised stores.
"""

from __future__ import annotations

import json

from toolbaton.platforms import get
from toolbaton.platforms.base import ReadOptions, WriteOptions
from toolbaton.platforms.claude_code import reader as claude_reader


def test_cursor_to_claude_and_back_preserves_the_conversation(cursor_env,
                                                              claude_env,
                                                              tmp_path):
    project = cursor_env["project"]
    cursor, claude = get("cursor"), get("claude-code")

    original = cursor.read(ReadOptions(project=project, source="sqlite"))
    assert len(original) == 1

    out = tmp_path / "out"
    claude.write(original, WriteOptions(project=project, out_dir=out,
                                        tools="blocks", target_version="1.0.0"))
    session = next((out / "sessions").glob("*.jsonl"))

    back = claude_reader.read_session(session, include_sidechains=False)
    assert back is not None
    # The preamble is a synthetic turn, so the round trip gains exactly one.
    assert len(back.messages) == len(original[0].messages) + 1
    assert "Remove duplicated setup" in back.title
    tools = [t for m in back.messages for t in m.tools]
    assert [t.name for t in tools] == ["Read"]


def test_claude_to_cursor_writes_a_readable_transcript(claude_env, tmp_path):
    from tests.fixtures.build import build_claude_session

    project = claude_env["project"]
    build_claude_session(claude_env["sessions"])

    conversations = get("claude-code").read(ReadOptions(project=project))
    assert len(conversations) == 1
    assert conversations[0].messages[1].tools[0].result == "wrote 12 lines"

    out = tmp_path / "out"
    get("cursor").write(conversations, WriteOptions(project=project, out_dir=out))
    transcript = next((out / "cursor-transcripts").glob("*/*.jsonl"))

    lines = [json.loads(line) for line in transcript.read_text().splitlines()]
    assert lines[0]["role"] == "user"
    assert "<user_query>" in lines[0]["message"]["content"][0]["text"]
    assert any(block.get("type") == "tool_use"
               for line in lines
               for block in line["message"]["content"])


def test_imported_sessions_are_not_re_exported(claude_env):
    from tests.fixtures.build import build_claude_session

    build_claude_session(claude_env["sessions"], imported=True)
    build_claude_session(claude_env["sessions"], imported=False)
    conversations = get("claude-code").read(
        ReadOptions(project=claude_env["project"]))
    # Re-exporting an import would multiply history on every round trip.
    assert len(conversations) == 1


def test_tool_results_only_turns_are_not_treated_as_user_messages(claude_env):
    from tests.fixtures.build import build_claude_session

    build_claude_session(claude_env["sessions"])
    conv = get("claude-code").read(ReadOptions(project=claude_env["project"]))[0]
    # The fixture's tool_result turn is protocol plumbing, not a human turn.
    assert [m.role for m in conv.messages] == ["user", "assistant", "assistant"]


def test_bundle_is_a_usable_source(cursor_env, claude_env, tmp_path):
    """A bundle must be replayable, since `export` tells the user it is."""
    project = cursor_env["project"]
    out = tmp_path / "out"

    exported = get("cursor").read(ReadOptions(project=project, source="sqlite"))
    from toolbaton import ir
    path = ir.write_bundle(exported, project, "cursor", out)

    replayed = get("bundle").read(ReadOptions(project=project, source=str(path)))
    assert [c.id for c in replayed] == [c.id for c in exported]
    assert [len(c.messages) for c in replayed] == [len(c.messages) for c in exported]

    # And it can drive a real write, which is the point of carrying the file.
    result = get("claude-code").write(
        replayed, WriteOptions(project=project, out_dir=tmp_path / "from-bundle"))
    assert any(p.suffix == ".jsonl" for p in result.files)


def test_bundle_source_accepts_a_directory(cursor_env, tmp_path):
    project = cursor_env["project"]
    out = tmp_path / "out"
    from toolbaton import ir
    ir.write_bundle(get("cursor").read(ReadOptions(project=project,
                                                   source="sqlite")),
                    project, "cursor", out)
    assert get("bundle").read(ReadOptions(project=project, source=str(out)))


def test_missing_bundle_fails_with_guidance(project, tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError, match="baton export"):
        get("bundle").read(ReadOptions(project=project,
                                       source=str(tmp_path / "nope.json")))
