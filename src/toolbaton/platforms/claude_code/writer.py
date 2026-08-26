"""Write IR conversations as Claude Code sessions.

Claude Code stores a session as JSONL at
`~/.claude/projects/<slug>/<sessionId>.jsonl`, one record per line, with the file
name matching the `sessionId` inside. Records emitted here:

    {"type": "summary",   "summary": ..., "leafUuid": ...}
    {"type": "user",      "message": {"role": "user", "content": [...]}, ...}
    {"type": "assistant", "message": {"role": "assistant", "content": [...]}, ...}

## The tool_use problem

Most agents record tool *calls* but not tool *results*. The Anthropic API rejects
an assistant `tool_use` block with no matching `tool_result`, so copying blocks
across verbatim produces a session that fails the moment it is resumed. Three
strategies, selected with `--tools`:

  text    (default) render each call as text inside the assistant turn. Always
          resumable; loses machine-readable structure.
  blocks  keep real `tool_use` blocks and synthesise the `tool_result` the API
          requires, using a recorded result where the source has one.
  drop    omit calls entirely. Smallest and most readable, least faithful.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from ...ir import Conversation
from ...redact import RedactionReport, redact_conversation
from ..base import WriteOptions, WriteResult
from .paths import FALLBACK_VERSION, detect_version, project_dir

#: Stable namespace, so re-running a migration reproduces the same uuids instead
#: of duplicating every message.
NS = uuid.UUID("6f0b3d4e-1f2a-4c5b-9d8e-7a6b5c4d3e2f")

DEFAULT_MODEL = "imported"
MAX_TOOL_INPUT = 2000
MAX_TOOL_RESULT = 4000
CAVEAT = ("Replay of a thread from another agent. Tool output is historical, "
          "not the current state of the tree.")


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "|".join(parts)))


def _clip(value, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} chars truncated]"


def _iso(value, fallback: datetime) -> str:
    if isinstance(value, str) and value:
        return value
    return fallback.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _tool_as_text(tool) -> str:
    header = tool.name
    if tool.source_name and tool.source_name != tool.name:
        header += f" (source: {tool.source_name})"
    out = [f"**[tool] {header}**", "", "```json",
           _clip(tool.input or {}, MAX_TOOL_INPUT), "```"]
    if tool.result:
        out += ["", "<details><summary>result</summary>", "", "```",
                _clip(tool.result, MAX_TOOL_RESULT), "```", "", "</details>"]
    return "\n".join(out)


def preamble(conv: Conversation, prefix: str, mode: str) -> str:
    """The synthetic opening turn, which doubles as the session's display name.

    The resume picker renders the first user message flattened onto one line, so
    the title has to come first — no HTML comment, no heading marker — or the
    picker shows the banner instead of the title.
    """
    headline = f"{prefix}{conv.title}".rstrip()
    if mode == "minimal":
        return headline

    if mode == "compact":
        bits = [f"source:{conv.id[:8]}"]
        started, last = (conv.created_at or "")[:10], (conv.updated_at or "")[:10]
        if started and last and started != last:
            bits.append(f"{started} → {last}")
        elif started or last:
            bits.append(started or last)
        if conv.mode:
            bits.append(conv.mode)
        if conv.lines_added or conv.lines_removed:
            bits.append(f"+{conv.lines_added}/-{conv.lines_removed}")
        if conv.files:
            bits.append(f"{len(conv.files)} files")
        return f"{headline}\n\n{' · '.join(bits)}\n\n{CAVEAT}"

    lines = [headline, "",
             f"- Source id: `{conv.id}`",
             f"- Started: {conv.created_at or 'unknown'}",
             f"- Last active: {conv.updated_at or 'unknown'}",
             f"- Source: {conv.source or 'unknown'}"]
    if conv.mode:
        lines.append(f"- Mode: {conv.mode}")
    if conv.lines_added or conv.lines_removed:
        lines.append(f"- Diff: +{conv.lines_added} / -{conv.lines_removed}")
    if conv.files:
        lines += ["", "Files this thread touched:"]
        lines += [f"- `{f}`" for f in conv.files[:25]]
        if len(conv.files) > 25:
            lines.append(f"- ... and {len(conv.files) - 25} more")
    lines += ["", CAVEAT]
    return "\n".join(lines)


def to_session(conv: Conversation, options: WriteOptions) -> list[dict]:
    """Build the JSONL records for one conversation."""
    records: list[dict] = []
    base = datetime.now(timezone.utc)
    if conv.created_at:
        with contextlib.suppress(ValueError):
            base = datetime.fromisoformat(conv.created_at.replace("Z", "+00:00"))

    common = {
        "isSidechain": False,
        "userType": "external",
        "cwd": str(options.project),
        "sessionId": conv.id,
        "version": options.target_version or detect_version() or FALLBACK_VERSION,
    }

    records.append({
        **common,
        "parentUuid": None,
        "type": "user",
        "message": {"role": "user",
                    "content": [{"type": "text",
                                 "text": preamble(conv, options.prefix,
                                                  options.mode)}]},
        "uuid": _uid(conv.id, "preamble"),
        "timestamp": _iso(conv.created_at, base),
    })
    parent = records[-1]["uuid"]

    for offset, msg in enumerate(conv.messages, start=1):
        stamp = _iso(msg.created_at, base + timedelta(seconds=offset))
        uid = _uid(conv.id, msg.id, msg.role)

        if msg.role == "user":
            records.append({
                **common, "parentUuid": parent, "type": "user",
                "message": {"role": "user",
                            "content": [{"type": "text",
                                         "text": msg.text or "(empty turn)"}]},
                "uuid": uid, "timestamp": stamp,
            })
            parent = uid
            continue

        content: list[dict] = []
        if options.include_thinking and msg.thinking:
            content.append({"type": "text",
                            "text": f"_(thinking)_\n\n{msg.thinking}"})
        if msg.text:
            content.append({"type": "text", "text": msg.text})

        pending: list[dict] = []
        if options.tools != "drop":
            for index, tool in enumerate(msg.tools):
                if options.tools == "blocks":
                    tid = "toolu_" + uuid.uuid5(
                        NS, f"{conv.id}|{msg.id}|{index}").hex[:24]
                    content.append({"type": "tool_use", "id": tid,
                                    "name": tool.name or "Bash",
                                    "input": tool.input or {}})
                    pending.append({
                        "type": "tool_result", "tool_use_id": tid,
                        "content": _clip(tool.result
                                         or "[result not recorded by the source agent]",
                                         MAX_TOOL_RESULT),
                        "is_error": tool.status == "error",
                    })
                else:
                    content.append({"type": "text", "text": _tool_as_text(tool)})

        if not content:
            continue

        records.append({
            **common, "parentUuid": parent, "type": "assistant",
            "message": {
                "id": "msg_" + uuid.uuid5(NS, uid).hex[:24],
                "type": "message", "role": "assistant",
                "model": conv.model or DEFAULT_MODEL,
                "content": content,
                "stop_reason": "tool_use" if pending else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
            "uuid": uid, "timestamp": stamp,
        })
        parent = uid

        if pending:
            # The API requires results to arrive as the next user turn.
            rid = _uid(conv.id, msg.id, "results")
            records.append({
                **common, "parentUuid": parent, "type": "user",
                "message": {"role": "user", "content": pending},
                "uuid": rid, "timestamp": stamp,
                "toolUseResult": "imported by tool-baton",
            })
            parent = rid

    if parent:
        records.insert(0, {"type": "summary",
                           "summary": f"{options.prefix}{conv.title}".rstrip(),
                           "leafUuid": parent})
    return records


def write(conversations: list[Conversation], options: WriteOptions) -> WriteResult:
    out_dir = options.out_dir / "sessions"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = WriteResult()
    report = RedactionReport()

    for conv in conversations:
        if options.redact:
            redact_conversation(conv, report)
        path = out_dir / f"{conv.id}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in to_session(conv, options):
                handle.write(json.dumps(record, ensure_ascii=False,
                                        default=str) + "\n")
        result.files.append(path)

    result.redactions = report.count
    if report.count:
        result.notes.append(f"sessions: {report.summary()}")
    result.notes.append(f"sessions land in {project_dir(options.project)} on install")
    return result
