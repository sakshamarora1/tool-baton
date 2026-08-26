"""Read Cursor's SQLite state into the IR.

Cursor uses two key-value tables in every `state.vscdb`:

  ItemTable     VS Code's own memento storage
  cursorDiskKV  Cursor's AI storage

The keys that matter, verified against a real installation:

  globalStorage/state.vscdb
    cursorDiskKV  composerData:<composerId>   one conversation's metadata
    cursorDiskKV  bubbleId:<composerId>:<id>  one message
    cursorDiskKV  composer.content.<sha256>   snapshot of an attached file

  workspaceStorage/<md5>/state.vscdb
    ItemTable     aiService.prompts           the prompts you typed

Conversations are *not* partitioned per workspace: every composer for every
project sits in the one global database. Attribution is therefore done per
conversation, from the file URIs a thread touched, with the `workspaceUris` that
Cursor stamps on each message as a fallback.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from ...ir import Conversation, Message, ToolCall
from ...util.db import DBSnapshot, connect_readonly, load_json
from ...util.paths import uri_to_path
from . import paths as cursor_paths

# Cursor's bubble `type` discriminator.
ROLE_USER = 1
ROLE_ASSISTANT = 2

#: Cursor tool name -> a name that reads naturally in other agents. Cosmetic.
TOOL_ALIASES = {
    "read_file": "Read",
    "edit_file": "Edit",
    "search_replace": "Edit",
    "create_file": "Write",
    "delete_file": "Bash",
    "run_terminal_cmd": "Bash",
    "codebase_search": "Grep",
    "grep_search": "Grep",
    "file_search": "Glob",
    "list_dir": "Bash",
    "web_search": "WebSearch",
    "fetch_rules": "Read",
    "todo_write": "TodoWrite",
}

MAX_TOOL_RESULT = 4000


def _ms_to_iso(ms) -> str | None:
    try:
        return (
            datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    except (TypeError, ValueError, OSError):
        return None


class CursorStore:
    """Snapshot-backed reader for Cursor's global storage."""

    def __init__(self, db: Path | None = None):
        self._snap = DBSnapshot(Path(db) if db else cursor_paths.global_db())
        self._conn = None

    def __enter__(self) -> CursorStore:
        path = self._snap.__enter__()
        self._conn = connect_readonly(path)
        self._conn.text_factory = bytes
        return self

    def __exit__(self, *exc) -> None:
        if self._conn:
            self._conn.close()
        self._snap.__exit__(*exc)

    def _kv(self, like: str) -> Iterator[tuple[str, dict | list]]:
        cur = self._conn.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE ?", (like,)
        )
        for key, value in cur:
            parsed = load_json(value)
            if parsed is not None:
                yield key.decode("utf-8", "replace"), parsed

    # -- conversations ------------------------------------------------------ #

    def conversations(self) -> Iterator[Conversation]:
        """Every thread still on disk, metadata only (messages load lazily)."""
        for key, data in self._kv("composerData:%"):
            if isinstance(data, dict):
                yield self._conversation(key.split(":", 1)[1], data)

    def _conversation(self, composer_id: str, data: dict) -> Conversation:
        order, roles = [], {}
        for header in data.get("fullConversationHeadersOnly") or []:
            if not isinstance(header, dict):
                continue
            bubble = header.get("bubbleId")
            if bubble:
                order.append(bubble)
                roles[bubble] = ("user" if header.get("type") == ROLE_USER
                                 else "assistant")
        plan = data.get("plan")
        return Conversation(
            id=composer_id,
            title=(data.get("name") or "").strip() or "Untitled chat",
            created_at=_ms_to_iso(data.get("createdAt")),
            updated_at=_ms_to_iso(data.get("lastUpdatedAt")),
            source="cursor-sqlite",
            mode=data.get("unifiedMode") or data.get("forceMode"),
            model=(data.get("modelConfig") or {}).get("modelName"),
            plan=plan.get("content") if isinstance(plan, dict) else None,
            todos=[t for t in (data.get("todos") or []) if isinstance(t, dict)],
            files=self._files_of(data),
            lines_added=int(data.get("totalLinesAdded") or 0),
            lines_removed=int(data.get("totalLinesRemoved") or 0),
            message_order=order,
            roles=roles,
        )

    @staticmethod
    def _files_of(data: dict) -> list[str]:
        """Every workspace file a thread read, attached, or edited.

        This is what makes per-project attribution possible, and it doubles as
        the raw material for the hot-file ranking in `knowledge.py`.
        """
        seen: dict[str, None] = {}

        def add(uri):
            path = uri_to_path(uri)
            if path:
                seen.setdefault(path, None)

        for uri in data.get("allAttachedFileCodeChunksUris") or []:
            add(uri)
        for uri in data.get("originalFileStates") or {}:
            add(uri)
        for uri in data.get("codeBlockData") or {}:
            add(uri)
        context = data.get("context") or {}
        for sel in context.get("fileSelections") or []:
            if isinstance(sel, dict):
                uri = sel.get("uri") or {}
                add(uri.get("fsPath") if isinstance(uri, dict) else uri)
        for sel in context.get("folderSelections") or []:
            if isinstance(sel, dict):
                add(sel.get("relativePath") or sel.get("uri"))
        return list(seen)

    # -- messages ----------------------------------------------------------- #

    def messages(self, composer_id: str) -> list[Message]:
        out: list[Message] = []
        for key, data in self._kv(f"bubbleId:{composer_id}:%"):
            if not isinstance(data, dict):
                continue
            thinking = data.get("thinking") or {}
            out.append(
                Message(
                    id=key.rsplit(":", 1)[1],
                    role="user" if data.get("type") == ROLE_USER else "assistant",
                    text=(data.get("text") or "").strip(),
                    thinking=((thinking.get("text") or "").strip()
                              if isinstance(thinking, dict) else ""),
                    tools=self._tools_of(data),
                    created_at=data.get("createdAt"),
                    workspaces=[
                        p for p in (uri_to_path(u)
                                    for u in data.get("workspaceUris") or []) if p
                    ],
                )
            )
        return out

    @staticmethod
    def _tools_of(bubble: dict) -> list[ToolCall]:
        data = bubble.get("toolFormerData")
        if not isinstance(data, dict) or not data.get("name"):
            return []
        name = data["name"]
        args = load_json(data.get("rawArgs")) or load_json(data.get("params")) or {}
        result = data.get("result")
        if isinstance(result, str) and len(result) > MAX_TOOL_RESULT:
            result = (result[:MAX_TOOL_RESULT]
                      + f"\n... [{len(result) - MAX_TOOL_RESULT} chars truncated]")
        return [
            ToolCall(
                name=TOOL_ALIASES.get(name, name),
                source_name=name,
                input=args if isinstance(args, dict) else {"raw": args},
                result=result if isinstance(result, str) else None,
                status=data.get("status"),
            )
        ]

    def load(self, conv: Conversation) -> Conversation:
        """Attach ordered, non-empty messages to a conversation."""
        by_id = {m.id: m for m in self.messages(conv.id)}
        ordered = [by_id[b] for b in conv.message_order if b in by_id]
        known = set(conv.message_order)
        # Bubbles missing from the header list are orphans; keep them rather
        # than silently dropping content.
        ordered += [m for bid, m in by_id.items() if bid not in known]
        for msg in ordered:
            if not msg.role and msg.id in conv.roles:
                msg.role = conv.roles[msg.id]
        conv.messages = [m for m in ordered if not m.is_empty]
        if not conv.files:
            conv.files = sorted({p for m in conv.messages for p in m.workspaces})
        return conv


def prompts(project: Path) -> list[str]:
    """`aiService.prompts` from every workspace overlapping `project`."""
    out: list[str] = []
    for workspace in cursor_paths.workspaces_under(project):
        with DBSnapshot(workspace.db) as snap:
            conn = connect_readonly(snap)
            try:
                row = conn.execute(
                    "SELECT value FROM ItemTable WHERE key = ?",
                    ("aiService.prompts",),
                ).fetchone()
            finally:
                conn.close()
        for item in load_json(row[0]) if row else []:
            if isinstance(item, dict) and item.get("text"):
                out.append(item["text"])
            elif isinstance(item, str) and item.strip():
                out.append(item)
    return out
