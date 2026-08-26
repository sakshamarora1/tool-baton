"""Write conversations as plain Markdown.

Not an agent — a destination. Every coding agent can read files, so a Markdown
archive is the one output that works everywhere and keeps working after the
agent that produced it is gone. In practice it carries more day-to-day value
than a resumable session: it is greppable, reviewable, and diffable.

Because it is meant to be committed, redaction matters most here.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...ir import Conversation
from ...redact import RedactionReport, redact_conversation
from ..base import WriteOptions, WriteResult

MAX_TOOL_INPUT = 2000
MAX_TOOL_RESULT = 4000


def _clip(value, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} chars truncated]"


def _tool_block(tool) -> str:
    header = tool.name
    if tool.source_name and tool.source_name != tool.name:
        header += f" (source: {tool.source_name})"
    out = [f"**[tool] {header}**", "", "```json",
           _clip(tool.input or {}, MAX_TOOL_INPUT), "```"]
    if tool.result:
        out += ["", "<details><summary>result</summary>", "", "```",
                _clip(tool.result, MAX_TOOL_RESULT), "```", "", "</details>"]
    return "\n".join(out)


def to_markdown(conv: Conversation) -> str:
    quote = chr(34)
    out = ["---",
           f'title: "{conv.title.replace(quote, chr(39))}"',
           f"source_id: {conv.id}",
           f"source: {conv.source or 'unknown'}",
           f"date: {conv.date}",
           f"last_active: {(conv.updated_at or '')[:19]}",
           f"messages: {len(conv.messages)}",
           "---", "", f"# {conv.title}", ""]

    if conv.files:
        out += ["## Files touched", ""]
        out += [f"- `{f}`" for f in conv.files[:40]]
        if len(conv.files) > 40:
            out.append(f"- ... and {len(conv.files) - 40} more")
        out.append("")
    if conv.plan:
        out += ["## Plan", "", conv.plan.strip(), ""]
    if conv.todos:
        out += ["## Todos", ""]
        for todo in conv.todos:
            mark = "x" if todo.get("status") == "completed" else " "
            out.append(f"- [{mark}] {todo.get('content', '')}")
        out.append("")

    out += ["## Conversation", ""]
    for msg in conv.messages:
        out.append(f"### {'User' if msg.role == 'user' else 'Assistant'}")
        if msg.created_at:
            out.append(f"<sub>{msg.created_at}</sub>")
        out.append("")
        if msg.thinking:
            out += ["<details><summary>thinking</summary>", "", msg.thinking, "",
                    "</details>", ""]
        if msg.text:
            out += [msg.text, ""]
        for tool in msg.tools:
            out += [_tool_block(tool), ""]
    return "\n".join(out).rstrip() + "\n"


def write_index(conversations: list[Conversation], names: dict[str, str],
                out_dir: Path) -> Path:
    rows = ["# Chat archive", "",
            f"{len(conversations)} conversation(s) migrated by tool-baton.", "",
            "| Date | Title | Msgs | Source | Transcript |",
            "|---|---|---|---|---|"]
    for conv in sorted(conversations, key=lambda c: c.created_at or "",
                       reverse=True):
        name = names[conv.id]
        title = conv.title.replace("|", "\\|")[:80]
        rows.append(f"| {conv.date} | {title} | {len(conv.messages)} | "
                    f"{conv.source or '?'} | [{name}]({name}) |")
    path = out_dir / "INDEX.md"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def write(conversations: list[Conversation], options: WriteOptions) -> WriteResult:
    out_dir = options.out_dir / "archive"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = WriteResult()
    report = RedactionReport()
    names: dict[str, str] = {}

    for conv in conversations:
        if options.redact:
            redact_conversation(conv, report)
        name = f"{conv.date}-{conv.slug}-{conv.id[:8]}.md"
        path = out_dir / name
        path.write_text(to_markdown(conv), encoding="utf-8")
        names[conv.id] = name
        result.files.append(path)

    result.files.append(write_index(conversations, names, out_dir))
    result.redactions = report.count
    if report.count:
        result.notes.append(f"archive: {report.summary()}")
    return result
