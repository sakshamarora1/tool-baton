"""Read Claude Code sessions into the IR.

This is the half that makes the tool bidirectional. Claude Code's own transcripts
are plain JSONL, one record per line, so reading them needs no database access at
all — a pleasant contrast to the agents that hide history in SQLite.

Records seen in real session files:

    {"type": "summary",         "summary": ..., "leafUuid": ...}
    {"type": "user",            "message": {"role": ..., "content": [...]}, ...}
    {"type": "assistant",       "message": {...}, ...}
    {"type": "queue-operation", ...}                  <- control, skipped

Content blocks: `text`, `thinking`, `tool_use`, `tool_result`. Results arrive in
a *later* user turn keyed by `tool_use_id`, so they are collected and stitched
back onto the call they belong to — which means Claude Code is one of the few
sources that can hand another agent real tool output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ...ir import Conversation, Message, ToolCall
from .paths import project_dir
from .retitle import is_imported, read_records

#: Tool-input keys that name a file, used to attribute a thread to a project.
PATH_KEYS = ("file_path", "path", "notebook_path", "target_file")
MAX_TOOL_RESULT = 4000

#: Claude Code wraps slash commands, hook output and system notes in XML-ish
#: tags inside the message text. They are noise in a title.
_WRAPPER_TAG = re.compile(
    r"</?(?:local-command-[a-z\-]+|command-(?:message|name|args)|"
    r"system-reminder|user-prompt-submit-hook|bash-(?:input|stdout|stderr))>")


def _clean_title(text: str) -> str:
    """Strip Claude Code's internal tags so a title reads like a title."""
    cleaned = _WRAPPER_TAG.sub(" ", text)
    cleaned = re.sub(r"^\s*Caveat:.*?(?=\S{4,})", "", cleaned, flags=re.S)
    return " ".join(cleaned.split())


def _blocks(record: dict) -> list[dict]:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def _result_text(block: dict) -> str | None:
    content = block.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(part.get("text", "") for part in content
                         if isinstance(part, dict))
    else:
        return None
    text = text.strip()
    if len(text) > MAX_TOOL_RESULT:
        dropped = len(text) - MAX_TOOL_RESULT
        text = text[:MAX_TOOL_RESULT] + f"\n... [{dropped} chars truncated]"
    return text or None


def read_session(path: Path, include_sidechains: bool = False) -> Conversation | None:
    """Parse one session file into a `Conversation`."""
    records = read_records(path)
    if not records:
        return None

    summary = next((str(r.get("summary", "")) for r in records
                    if r.get("type") == "summary"), "")

    # Pass one: collect tool results so they can be attached to their calls.
    results: dict[str, tuple[str | None, bool]] = {}
    for record in records:
        if record.get("type") != "user":
            continue
        for block in _blocks(record):
            if block.get("type") == "tool_result" and block.get("tool_use_id"):
                results[block["tool_use_id"]] = (_result_text(block),
                                                 bool(block.get("is_error")))

    messages: list[Message] = []
    files: dict[str, None] = {}
    session_id = path.stem
    cwd = None

    for record in records:
        kind = record.get("type")
        if kind not in ("user", "assistant"):
            continue
        if record.get("isSidechain") and not include_sidechains:
            continue
        session_id = record.get("sessionId") or session_id
        cwd = record.get("cwd") or cwd

        texts: list[str] = []
        thinking: list[str] = []
        tools: list[ToolCall] = []
        only_results = True

        for block in _blocks(record):
            btype = block.get("type")
            if btype == "text":
                only_results = False
                if (block.get("text") or "").strip():
                    texts.append(block["text"].strip())
            elif btype == "thinking":
                only_results = False
                value = (block.get("thinking") or block.get("text") or "").strip()
                if value:
                    thinking.append(value)
            elif btype == "tool_use":
                only_results = False
                payload = block.get("input")
                payload = payload if isinstance(payload, dict) else {"raw": payload}
                result, errored = results.get(block.get("id", ""), (None, False))
                tools.append(ToolCall(name=block.get("name") or "unknown",
                                      source_name=block.get("name") or "",
                                      input=payload, result=result,
                                      status="error" if errored else "completed"))
                for key in PATH_KEYS:
                    value = payload.get(key)
                    if isinstance(value, str) and value.startswith("/"):
                        files.setdefault(value, None)

        # A user turn carrying nothing but tool_result blocks is protocol
        # plumbing, not a human turn — the results are already on their calls.
        if only_results and kind == "user":
            continue
        if not texts and not thinking and not tools:
            continue

        messages.append(Message(
            id=str(record.get("uuid") or f"{len(messages):05d}"),
            role=kind,
            text="\n\n".join(texts),
            thinking="\n\n".join(thinking),
            tools=tools,
            created_at=record.get("timestamp"),
            workspaces=[cwd] if cwd else [],
        ))

    if not messages:
        return None

    title = summary
    for prefix in ("[baton] ", "[Cursor] "):
        if title.startswith(prefix):
            title = title[len(prefix):]
    if not title:
        first = next((m.text for m in messages if m.role == "user" and m.text), "")
        title = _clean_title(first)[:70] or "Untitled session"

    conv = Conversation(
        id=str(session_id),
        title=title,
        created_at=messages[0].created_at,
        updated_at=messages[-1].created_at,
        source="claude-code",
    )
    conv.messages = messages
    conv.files = sorted(files)
    if not conv.files and cwd:
        conv.files = [cwd]
    return conv


def read_project(project: Path, skip_imported: bool = True,
                 include_sidechains: bool = False) -> list[Conversation]:
    """Every session Claude Code has stored for `project`.

    Sessions this package wrote are skipped by default: re-exporting an import
    would multiply the same history on every round trip.
    """
    directory = project_dir(project)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.jsonl")):
        if skip_imported and is_imported(read_records(path)):
            continue
        conv = read_session(path, include_sidechains=include_sidechains)
        if conv:
            out.append(conv)
    return out


def read_prompt_history(project: Path) -> list[str]:
    """Prompts from `~/.claude/history.jsonl` belonging to `project`."""
    from .paths import history_file

    path = history_file()
    if not path.is_file():
        return []
    target = str(Path(project).resolve())
    out = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("project") == target and record.get("display"):
                out.append(record["display"])
    return out
