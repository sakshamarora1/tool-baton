"""The canonical conversation model every adapter talks to.

An adapter author never has to understand the *other* agent's format. A reader
turns its agent's storage into `Conversation` objects; a writer turns
`Conversation` objects into its agent's storage. Adding an agent is therefore one
reader plus one writer, not a pairwise integration with everything else.

The model is deliberately small, and holds only what actually survives a
migration between coding agents:

    Conversation   a thread: title, timestamps, the files it touched, messages
    Message        one turn: role, text, reasoning, tool calls
    ToolCall       a single tool invocation, and its result if the source kept one

Anything richer (checkpoints, worktrees, embeddings, inline-edit history) is
agent-specific and does not round-trip, so it is not modelled.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1

# Canonical tool names. Adapters map their agent's vocabulary onto these so a
# transcript reads naturally in whichever agent it lands in.
CANONICAL_TOOLS = {
    "read", "write", "edit", "shell", "search", "glob", "web", "todo", "task",
}


@dataclass
class ToolCall:
    name: str                       # canonical-ish display name, e.g. "Read"
    source_name: str = ""           # what the origin agent called it
    input: dict = field(default_factory=dict)
    result: str | None = None       # None when the source did not record one
    status: str | None = None

    @property
    def has_result(self) -> bool:
        return bool(self.result)


@dataclass
class Message:
    id: str
    role: str                       # "user" | "assistant"
    text: str = ""
    thinking: str = ""
    tools: list[ToolCall] = field(default_factory=list)
    created_at: str | None = None   # ISO 8601, UTC
    workspaces: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.text.strip() or self.thinking.strip() or self.tools)


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    source: str = ""                # which adapter produced this, for provenance
    mode: str | None = None
    model: str | None = None
    plan: str | None = None
    todos: list[dict] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0
    messages: list[Message] = field(default_factory=list)
    # Ordering/role hints the reader recovered before loading message bodies.
    message_order: list[str] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", (self.title or "untitled").lower()).strip("-")
        return (base or "untitled")[:60]

    @property
    def date(self) -> str:
        return (self.created_at or "")[:10] or "undated"

    def touches(self, root: Path) -> bool:
        """Does this thread belong to `root`?

        Agents that keep every project's chats in one store need per-conversation
        attribution, and the file paths a thread actually read are the most
        reliable signal available.
        """
        prefix = str(Path(root).resolve())
        return any(f == prefix or f.startswith(prefix + "/") for f in self.files)

    @property
    def tool_calls(self) -> int:
        return sum(len(m.tools) for m in self.messages)


# --------------------------------------------------------------------------- #
# Merging several readings of the same history
# --------------------------------------------------------------------------- #


def merge_sources(primary: list[Conversation],
                  overlay: list[Conversation]) -> list[Conversation]:
    """Combine two readings, preferring `overlay`'s message bodies.

    Some agents keep the same thread in two places at different fidelity — for
    example Cursor has both a complete-but-noisy SQLite store and cleaner JSONL
    transcripts covering only recent threads. Where both describe one id, take
    the cleaner bodies and the richer metadata.
    """
    by_id = {c.id: c for c in primary}
    out: dict[str, Conversation] = dict(by_id)
    for conv in overlay:
        base = by_id.get(conv.id)
        if base is None:
            out[conv.id] = conv
            continue
        base.messages = conv.messages
        base.source = f"{conv.source}+{base.source}" if base.source else conv.source
        if not base.files:
            base.files = conv.files
        out[conv.id] = base
    return sorted(out.values(), key=lambda c: c.created_at or "")


# --------------------------------------------------------------------------- #
# Portable bundle
# --------------------------------------------------------------------------- #


def to_bundle(conversations: list[Conversation], project: Path,
              source: str) -> dict:
    """A self-describing JSON document holding an entire migration.

    This is the seam that makes the tool useful beyond the agents it knows: dump
    a bundle from one machine, write it anywhere, or hand it to a writer for an
    agent that did not exist when the bundle was made.
    """
    return {
        "schema": SCHEMA_VERSION,
        "project": str(project),
        "source": source,
        "conversations": [asdict(c) for c in conversations],
    }


def from_bundle(data: dict) -> list[Conversation]:
    schema = data.get("schema")
    if schema != SCHEMA_VERSION:
        raise ValueError(
            f"bundle schema {schema!r} is not supported by this version "
            f"(expected {SCHEMA_VERSION})"
        )
    out = []
    for raw in data.get("conversations") or []:
        messages = [
            Message(
                **{
                    **{k: v for k, v in m.items() if k != "tools"},
                    "tools": [ToolCall(**t) for t in (m.get("tools") or [])],
                }
            )
            for m in raw.get("messages") or []
        ]
        out.append(Conversation(**{**{k: v for k, v in raw.items()
                                     if k != "messages"},
                                   "messages": messages}))
    return out


def write_bundle(conversations: list[Conversation], project: Path, source: str,
                 out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "bundle.json"
    payload = to_bundle(conversations, project, source)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n",
                    encoding="utf-8")
    return path


def read_bundle(path: Path) -> list[Conversation]:
    return from_bundle(json.loads(Path(path).read_text(encoding="utf-8")))
