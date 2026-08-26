"""Synthesise agent storage for tests.

Real fixtures cannot be committed: they are private chat history. So the tests
build throwaway stores matching the real schemas instead. That keeps the suite
runnable on any machine and in CI, and it documents the formats the adapters
depend on — if an agent changes its layout, the fixture is where you encode it.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

COMPOSER_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
COMPOSER_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"


def _ms(days_ago: float) -> int:
    return int((time.time() - days_ago * 86400) * 1000)


def build_cursor_db(root: Path, project: Path) -> Path:
    """A Cursor `globalStorage/state.vscdb` with two conversations.

    Mirrors the real layout: an `ItemTable`/`cursorDiskKV` pair, conversations as
    `composerData:<id>`, messages as `bubbleId:<id>:<bubble>`, and the ordering
    carried separately in `fullConversationHeadersOnly`.
    """
    db = root / "globalStorage" / "state.vscdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, "
                 "value BLOB)")
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, "
                 "value BLOB)")

    def put(table: str, key: str, value) -> None:
        conn.execute(f"INSERT INTO {table} (key, value) VALUES (?, ?)",
                     (key, json.dumps(value)))

    target = project / "src" / "app.py"
    other = project / "src" / "util.py"

    # -- conversation A: in this project, with a tool call and a secret ------ #
    bubbles_a = [
        ("11111111-1111-4111-8111-111111111111", 1,
         {"text": "Refactor @src/app.py to remove the duplicated setup.",
          "workspaceUris": [f"file://{project}"]}),
        ("22222222-2222-4222-8222-222222222222", 2,
         {"text": "Looking at the file now.",
          "thinking": {"text": "The setup block appears twice."},
          "toolFormerData": {
              "name": "read_file", "status": "completed",
              "rawArgs": json.dumps({"target_file": "src/app.py"}),
              "result": "OPENAI_API_KEY=sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
          }}),
        ("33333333-3333-4333-8333-333333333333", 2,
         {"text": "Done — the duplicate block is gone."}),
        # An empty streaming placeholder, which the reader must drop.
        ("44444444-4444-4444-8444-444444444444", 2, {}),
    ]
    put("cursorDiskKV", f"composerData:{COMPOSER_A}", {
        "composerId": COMPOSER_A,
        "name": "Remove duplicated setup",
        "createdAt": _ms(10), "lastUpdatedAt": _ms(9),
        "unifiedMode": "agent",
        "modelConfig": {"modelName": "test-model"},
        "totalLinesAdded": 12, "totalLinesRemoved": 30,
        "plan": {"content": "# Plan\n\nDelete the second setup block."},
        "todos": [{"content": "delete duplicate", "status": "completed"}],
        "allAttachedFileCodeChunksUris": [f"file://{target}"],
        "originalFileStates": {f"file://{other}": {"content": "x"}},
        "codeBlockData": {},
        "context": {"fileSelections": [{"uri": {"fsPath": str(target)}}]},
        "fullConversationHeadersOnly": [
            {"bubbleId": bid, "type": kind} for bid, kind, _ in bubbles_a
        ],
    })
    for bid, kind, payload in bubbles_a:
        put("cursorDiskKV", f"bubbleId:{COMPOSER_A}:{bid}",
            {"bubbleId": bid, "type": kind, **payload})

    # -- conversation B: a different project, must be filtered out ---------- #
    elsewhere = Path("/somewhere/else/other.py")
    put("cursorDiskKV", f"composerData:{COMPOSER_B}", {
        "composerId": COMPOSER_B, "name": "Unrelated thread",
        "createdAt": _ms(5), "lastUpdatedAt": _ms(5),
        "allAttachedFileCodeChunksUris": [f"file://{elsewhere}"],
        "fullConversationHeadersOnly": [
            {"bubbleId": "55555555-5555-4555-8555-555555555555", "type": 1}],
    })
    put("cursorDiskKV",
        f"bubbleId:{COMPOSER_B}:55555555-5555-4555-8555-555555555555",
        {"bubbleId": "55555555-5555-4555-8555-555555555555", "type": 1,
         "text": "Unrelated question"})

    conn.commit()
    conn.close()
    return db


def build_cursor_workspace(root: Path, project: Path) -> Path:
    """A `workspaceStorage/<hash>` holding prompt history."""
    ws = root / "workspaceStorage" / "0123456789abcdef0123456789abcdef"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.json").write_text(json.dumps({"folder": f"file://{project}"}))
    conn = sqlite3.connect(ws / "state.vscdb")
    conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, "
                 "value BLOB)")
    conn.execute("INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                 ("aiService.prompts",
                  json.dumps([{"text": "newest prompt", "commandType": 4},
                              {"text": "older prompt", "commandType": 4}])))
    conn.commit()
    conn.close()
    return ws / "state.vscdb"


def build_cursor_transcript(cursor_home: Path, project_slug: str,
                            composer_id: str = COMPOSER_A) -> Path:
    """A Cursor `agent-transcripts` file, the higher-fidelity source."""
    directory = (cursor_home / "projects" / project_slug / "agent-transcripts"
                 / composer_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{composer_id}.jsonl"
    lines = [
        {"role": "user", "message": {"content": [{
            "type": "text",
            "text": "<timestamp>Monday, Jun 1, 2026, 12:04 PM (UTC+2)</timestamp>\n"
                    "<user_query>\nRefactor the setup block.\n</user_query>"}]}},
        {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "Reading the file."},
            {"type": "tool_use", "name": "Read",
             "input": {"path": "/tmp/project/src/app.py"}}]}},
        {"type": "status", "status": "done"},   # control line, must be skipped
        {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "Refactored."}]}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def build_claude_session(project_dir: Path, session_id: str | None = None,
                         imported: bool = False) -> Path:
    """A Claude Code session file, native or previously imported."""
    session_id = session_id or str(uuid.uuid4())
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"

    common = {"isSidechain": False, "userType": "external",
              "cwd": "/tmp/project", "sessionId": session_id, "version": "2.1.0"}
    first_text = ("[baton] Imported thread" if imported
                  else "Add a health check endpoint")
    records = [
        {"type": "queue-operation", "operation": "enqueue"},  # control, skipped
        {**common, "parentUuid": None, "type": "user", "uuid": "u1",
         "timestamp": "2026-06-01T10:00:00.000Z",
         "message": {"role": "user",
                     "content": [{"type": "text", "text": first_text}]}},
        {**common, "parentUuid": "u1", "type": "assistant", "uuid": "a1",
         "timestamp": "2026-06-01T10:00:05.000Z",
         "message": {"role": "assistant", "model": "test",
                     "content": [
                         {"type": "thinking", "thinking": "Need a new route."},
                         {"type": "text", "text": "Adding the route."},
                         {"type": "tool_use", "id": "toolu_1", "name": "Write",
                          "input": {"file_path": "/tmp/project/src/health.py"}}]}},
        {**common, "parentUuid": "a1", "type": "user", "uuid": "u2",
         "timestamp": "2026-06-01T10:00:06.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_1",
              "content": "wrote 12 lines", "is_error": False}]}},
        {**common, "parentUuid": "u2", "type": "assistant", "uuid": "a2",
         "timestamp": "2026-06-01T10:00:09.000Z",
         "message": {"role": "assistant", "model": "test",
                     "content": [{"type": "text", "text": "Endpoint added."}]}},
    ]
    if imported:
        records.insert(0, {"type": "summary", "summary": "[baton] Imported thread",
                           "leafUuid": "a2"})
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path
