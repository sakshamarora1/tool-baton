"""The Cursor adapter, against a synthesised store."""

from __future__ import annotations

from tests.fixtures.build import COMPOSER_A
from toolbaton.platforms import get
from toolbaton.platforms.base import ReadOptions
from toolbaton.platforms.cursor import paths as cursor_paths
from toolbaton.platforms.cursor import reader


def test_reads_only_conversations_belonging_to_the_project(cursor_env):
    options = ReadOptions(project=cursor_env["project"], source="sqlite")
    conversations = get("cursor").read(options)
    # The fixture holds a second thread scoped to an unrelated directory.
    assert [c.id for c in conversations] == [COMPOSER_A]


def test_metadata_and_ordering_survive(cursor_env):
    conv = get("cursor").read(
        ReadOptions(project=cursor_env["project"], source="sqlite"))[0]
    assert conv.title == "Remove duplicated setup"
    assert conv.mode == "agent"
    assert conv.model == "test-model"
    assert (conv.lines_added, conv.lines_removed) == (12, 30)
    assert conv.plan.startswith("# Plan")
    assert conv.todos[0]["status"] == "completed"
    assert [m.role for m in conv.messages] == ["user", "assistant", "assistant"]


def test_empty_streaming_placeholders_are_dropped(cursor_env):
    conv = get("cursor").read(
        ReadOptions(project=cursor_env["project"], source="sqlite"))[0]
    # The fixture includes one bubble with no text, thinking or tool call.
    assert all(not m.is_empty for m in conv.messages)
    assert len(conv.messages) == 3


def test_tool_calls_are_named_and_carry_their_result(cursor_env):
    conv = get("cursor").read(
        ReadOptions(project=cursor_env["project"], source="sqlite"))[0]
    tools = [t for m in conv.messages for t in m.tools]
    assert len(tools) == 1
    assert tools[0].name == "Read"              # mapped from read_file
    assert tools[0].source_name == "read_file"
    assert tools[0].input["target_file"] == "src/app.py"


def test_thinking_is_captured(cursor_env):
    conv = get("cursor").read(
        ReadOptions(project=cursor_env["project"], source="sqlite"))[0]
    assert any("appears twice" in m.thinking for m in conv.messages)


def test_prompt_history_is_recovered(cursor_env):
    prompts = reader.prompts(cursor_env["project"])
    assert prompts == ["newest prompt", "older prompt"]


def test_transcripts_are_preferred_over_sqlite_bodies(cursor_env):
    from tests.fixtures.build import build_cursor_transcript

    build_cursor_transcript(cursor_env["cursor_home"],
                            cursor_paths.project_slug(cursor_env["project"]))
    conversations = get("cursor").read(
        ReadOptions(project=cursor_env["project"], source="auto"))
    conv = next(c for c in conversations if c.id == COMPOSER_A)
    # Metadata still comes from SQLite; bodies from the transcript.
    assert conv.title == "Remove duplicated setup"
    assert "transcript" in conv.source
    assert any("Refactored" in m.text for m in conv.messages)


def test_the_source_database_is_never_modified(cursor_env):
    db = cursor_paths.global_db()
    before = db.stat().st_mtime_ns, db.stat().st_size
    get("cursor").read(ReadOptions(project=cursor_env["project"], source="sqlite"))
    assert (db.stat().st_mtime_ns, db.stat().st_size) == before
