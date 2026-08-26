"""Read and write Cursor's own agent transcripts.

`~/.cursor/projects/<slug>/agent-transcripts/<id>/<id>.jsonl` is already close to
Anthropic message shape, which makes it both the best *source* Cursor offers and
the safest *destination* for writing history back in. One JSON object per line:

    {"role": "user",      "message": {"content": [{"type": "text", ...}]}}
    {"role": "assistant", "message": {"content": [{"type": "text", ...},
                                                  {"type": "tool_use", ...}]}}
    {"type": "...", "status": "...", "error": ...}     <- control line, skipped

Two things are missing relative to a full session:

  * `tool_result` blocks — Cursor records the call, never the result
  * per-message timestamps — only the user turn carries one, inside a
    `<timestamp>` tag in the text
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ...ir import Conversation, Message, ToolCall
from .paths import transcripts_dir
from .reader import TOOL_ALIASES

_TIMESTAMP = re.compile(r"<timestamp>(.*?)</timestamp>\s*", re.S)
_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
_TS_FORMATS = ("%A, %b %d, %Y, %I:%M %p", "%A, %B %d, %Y, %I:%M %p")


def _parse_timestamp(raw: str) -> str | None:
    cleaned = re.sub(r"\s*\(UTC[^)]*\)", "", raw).strip()
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return None


def _unwrap_user_text(text: str) -> tuple[str, str | None]:
    """Strip Cursor's `<timestamp>` / `<user_query>` envelope off a user turn."""
    stamp = None
    match = _TIMESTAMP.search(text)
    if match:
        stamp = _parse_timestamp(match.group(1))
        text = _TIMESTAMP.sub("", text, count=1)
    query = _USER_QUERY.search(text)
    if query:
        # Anything outside the tag is Cursor-injected context, not the user's words.
        text = query.group(1)
    return text.strip(), stamp


def transcript_files(project: Path) -> list[Path]:
    root = transcripts_dir(project)
    return sorted(root.glob("*/*.jsonl")) if root.is_dir() else []


def read_transcript(path: Path) -> Conversation | None:
    """Parse one transcript file into a `Conversation`.

    The composer id is the file name, which lets the caller line the transcript
    up with the richer metadata in the SQLite store.
    """
    messages: list[Message] = []
    first_stamp = last_stamp = None
    index = 0

    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            role = record.get("role")
            if role not in ("user", "assistant"):
                continue  # control/status line

            content = (record.get("message") or {}).get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            if not isinstance(content, list):
                continue

            texts: list[str] = []
            tools: list[ToolCall] = []
            stamp = None

            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind in ("text", "thinking"):
                    raw = block.get("text") or block.get("thinking") or ""
                    if role == "user":
                        raw, stamp = _unwrap_user_text(raw)
                    if raw.strip():
                        texts.append(raw.strip())
                elif kind == "tool_use":
                    name = block.get("name") or "unknown"
                    payload = block.get("input")
                    tools.append(
                        ToolCall(
                            name=TOOL_ALIASES.get(name, name),
                            source_name=name,
                            input=payload if isinstance(payload, dict)
                            else {"raw": payload},
                        )
                    )

            if not texts and not tools:
                continue
            if stamp:
                first_stamp = first_stamp or stamp
                last_stamp = stamp

            index += 1
            messages.append(
                Message(id=f"{index:05d}", role=role, text="\n\n".join(texts),
                        tools=tools, created_at=stamp)
            )

    if not messages:
        return None

    first_user = next((m.text for m in messages if m.role == "user" and m.text), "")
    mtime = (datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
             .isoformat(timespec="milliseconds").replace("+00:00", "Z"))

    conv = Conversation(
        id=path.stem,
        title=" ".join(first_user.split())[:70] or "Untitled chat",
        created_at=first_stamp or mtime,
        updated_at=last_stamp or mtime,
        source="cursor-transcript",
    )
    conv.messages = messages
    conv.files = sorted({
        str(value)
        for m in messages for t in m.tools
        for key, value in (t.input or {}).items()
        if key in ("path", "target_file", "file_path", "relativeWorkspacePath")
        and isinstance(value, str)
    })
    return conv


def load_transcripts(project: Path) -> list[Conversation]:
    out = []
    for path in transcript_files(project):
        conv = read_transcript(path)
        if conv:
            out.append(conv)
    return out


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def write_transcript(conv: Conversation, out_dir: Path) -> Path:
    """Emit a conversation in Cursor's transcript shape.

    This is how history travels *into* Cursor. It lands in a directory Cursor
    already reads from, so the thread is `@`-mentionable, without this package
    ever writing to Cursor's database.
    """
    target = out_dir / conv.id
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{conv.id}.jsonl"

    with path.open("w", encoding="utf-8") as handle:
        for msg in conv.messages:
            blocks: list[dict] = []
            if msg.role == "user":
                stamp = msg.created_at or conv.created_at or ""
                text = (f"<timestamp>{stamp}</timestamp>\n"
                        f"<user_query>\n{msg.text}\n</user_query>")
                blocks.append({"type": "text", "text": text})
            else:
                if msg.thinking:
                    blocks.append({"type": "text",
                                   "text": f"(thinking)\n\n{msg.thinking}"})
                if msg.text:
                    blocks.append({"type": "text", "text": msg.text})
                for tool in msg.tools:
                    blocks.append({"type": "tool_use", "name": tool.name,
                                   "input": tool.input})
            if not blocks:
                continue
            handle.write(json.dumps({"role": msg.role,
                                     "message": {"content": blocks}},
                                    ensure_ascii=False, default=str) + "\n")
    return path
