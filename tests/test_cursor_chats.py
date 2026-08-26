"""Cursor's blob chat store.

The only Cursor source that keeps tool *results*, and the only one whose message
order lives in a separate node rather than in the records themselves.
"""

from __future__ import annotations

from tests.fixtures.build import COMPOSER_A, build_cursor_chat_store
from toolbaton.platforms import get
from toolbaton.platforms.base import ReadOptions
from toolbaton.platforms.cursor import chats


def _conversation(cursor_env):
    build_cursor_chat_store(cursor_env["cursor_home"], cursor_env["project"])
    return chats.load_chats(cursor_env["project"])[0]


def test_a_session_is_attributed_by_its_working_directory(cursor_env):
    build_cursor_chat_store(cursor_env["cursor_home"], cursor_env["project"])
    build_cursor_chat_store(cursor_env["cursor_home"],
                            cursor_env["project"].parent / "elsewhere",
                            session_id="cccccccc-3333-4333-8333-cccccccccccc")
    found = chats.load_chats(cursor_env["project"])
    assert [c.id for c in found] == [COMPOSER_A]


def test_message_order_comes_from_the_root_node(cursor_env):
    conv = _conversation(cursor_env)
    assert [m.role for m in conv.messages] == ["user", "assistant", "assistant"]
    assert conv.messages[0].text == "Add a health check endpoint."
    assert conv.messages[-1].text == "Endpoint added."


def test_the_longest_node_wins_over_the_recorded_root(cursor_env):
    # `latestRootBlobId` names the agent's live context: after a summarise it
    # points at a stub whose history has been replaced by a précis. Following it
    # would archive the summary and discard the thread it stands for.
    conv = _conversation(cursor_env)
    assert not any("Previous conversation summary" in m.text
                   for m in conv.messages)
    assert len(conv.messages) == 3


def test_tool_results_are_paired_back_onto_their_call(cursor_env):
    # A tool_use with no matching result makes a written Claude Code session
    # unresumable, so this is what lets `--tools blocks` be used safely.
    conv = _conversation(cursor_env)
    tools = [t for m in conv.messages for t in m.tools]
    assert len(tools) == 1
    assert tools[0].name == "Write"
    assert tools[0].result == "wrote 12 lines"
    assert tools[0].has_result


def test_reasoning_becomes_thinking(cursor_env):
    conv = _conversation(cursor_env)
    assert any("new route is needed" in m.thinking for m in conv.messages)


def test_the_injected_context_turn_is_not_archived_as_a_prompt(cursor_env):
    # This store splits Cursor's preamble into its own user record; the JSONL
    # transcripts bundle it with the prompt instead.
    conv = _conversation(cursor_env)
    assert not any("<user_info>" in m.text for m in conv.messages)


def test_system_prompt_is_not_part_of_the_conversation(cursor_env):
    conv = _conversation(cursor_env)
    assert all(m.role in ("user", "assistant") for m in conv.messages)


def test_metadata_and_touched_files_survive(cursor_env):
    conv = _conversation(cursor_env)
    assert conv.title == "Add a health check"
    assert conv.model == "test-model"
    assert conv.source == "cursor-chats"
    assert conv.files == [str(cursor_env["project"] / "src" / "health.py")]


def test_prompt_history_is_flipped_to_oldest_first(cursor_env):
    build_cursor_chat_store(cursor_env["cursor_home"], cursor_env["project"])
    assert chats.prompts(cursor_env["project"]) == ["older chat prompt",
                                                    "newest chat prompt"]


def test_chats_bodies_win_when_every_source_has_the_thread(cursor_env):
    from tests.fixtures.build import build_cursor_transcript
    from toolbaton.platforms.cursor import paths as cursor_paths

    build_cursor_transcript(cursor_env["cursor_home"],
                            cursor_paths.project_slug(cursor_env["project"]))
    build_cursor_chat_store(cursor_env["cursor_home"], cursor_env["project"])
    conversations = get("cursor").read(
        ReadOptions(project=cursor_env["project"], source="auto"))
    conv = next(c for c in conversations if c.id == COMPOSER_A)
    # Metadata still comes from SQLite; bodies, and the tool result, from here.
    assert conv.title == "Remove duplicated setup"
    assert "cursor-chats" in conv.source
    assert any(t.has_result for m in conv.messages for t in m.tools)


def test_the_source_database_is_never_modified(cursor_env):
    directory = build_cursor_chat_store(cursor_env["cursor_home"],
                                        cursor_env["project"])
    db = directory / "store.db"
    before = db.stat().st_mtime_ns, db.stat().st_size
    chats.load_chats(cursor_env["project"])
    assert (db.stat().st_mtime_ns, db.stat().st_size) == before
