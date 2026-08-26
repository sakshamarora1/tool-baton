"""Read Cursor's blob chat store.

`~/.cursor/chats/<hash>/<agentId>/` is the highest-fidelity record Cursor keeps,
and the only one of the three that stores **tool results**. That matters beyond
completeness: an assistant `tool_use` block with no matching `tool_result` makes
a written Claude Code session unresumable, so a thread sourced here can be
written with `--tools blocks` without synthesising anything.

Three files per session:

    meta.json           {"cwd": ..., "title": ..., "createdAtMs": ...}
    store.db            blobs(id, data) + meta(key, value)
    prompt_history.json ["most recent prompt", ...]

`cwd` is why this reader needs no path translation at all: attribution is an
exact string rather than an inference from the file URIs a thread touched.

The database is a content-addressed store, read in three hops:

  1. the single `meta` row holds **hex-encoded** JSON naming `latestRootBlobId`
  2. a *node* blob is a protobuf whose field 1 repeats a 32-byte digest per
     message, in conversation order
  3. each digest resolves to a blob of plain JSON — one `role` record

Ordering lives only in step 2, so a node has to be parsed even though a schema
for it is not published. Field 1 is all this needs, which a varint reader covers
in a few lines; anything else in the node is skipped.

**`latestRootBlobId` is not the conversation.** Every turn writes a new node
listing the whole sequence so far, so a session holds hundreds of cumulative
snapshots, and the id in `meta` names the agent's *live context* rather than its
history. After `/summarize` those differ completely: on a real 184-message thread
the recorded id pointed at a three-message context — a system prompt and a
"[Previous conversation summary]" turn — while the full thread sat in an
unreferenced node. Trusting it would silently archive the summary and discard
everything it replaced, so the reader takes the node covering the most messages
and falls back to the recorded id only to break a tie.

`meta` also carries a `blobEncryptionKey`, but the blobs on disk are cleartext —
it appears to be for cloud sync. Nothing here assumes either way: a blob that
does not parse as JSON is treated as a node rather than failing the thread.
"""

from __future__ import annotations

import binascii
import json
from dataclasses import dataclass
from pathlib import Path

from ...ir import Conversation, Message, ToolCall
from ...util.db import DBSnapshot, connect_readonly, load_json
from .paths import cursor_home
from .reader import MAX_TOOL_RESULT, TOOL_ALIASES, _ms_to_iso
from .transcripts import _unwrap_user_text

#: Blob ids are SHA-256 digests, so a root-node entry is exactly this long.
DIGEST_BYTES = 32

#: Keys naming a file in a tool's arguments, used to recover the files a thread
#: touched. Kept in step with `transcripts.py`.
_PATH_KEYS = ("path", "target_file", "file_path", "relativeWorkspacePath")


def chats_dir() -> Path:
    return cursor_home() / "chats"


# --------------------------------------------------------------------------- #
# The content-addressed store
# --------------------------------------------------------------------------- #


def _varint(data: bytes, index: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, index
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def blob_refs(node: bytes) -> list[str]:
    """The blob ids a root node lists, in conversation order.

    Only protobuf field 1 is of interest, so every other field is stepped over
    rather than modelled. A malformed tail stops the walk and keeps what was read
    up to that point, which is better than losing the whole thread.
    """
    refs: list[str] = []
    index = 0
    while index < len(node):
        try:
            key, index = _varint(node, index)
            field_number, wire = key >> 3, key & 7
            if wire == 2:
                length, index = _varint(node, index)
                chunk = node[index:index + length]
                index += length
                if field_number == 1 and len(chunk) == DIGEST_BYTES:
                    refs.append(chunk.hex())
            elif wire == 0:
                _, index = _varint(node, index)
            elif wire == 5:
                index += 4
            elif wire == 1:
                index += 8
            else:
                break
        except (IndexError, ValueError):
            break
    return refs


def _store_meta(conn) -> dict:
    row = conn.execute("SELECT value FROM meta LIMIT 1").fetchone()
    if not row or row[0] is None:
        return {}
    value = row[0]
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    try:
        return json.loads(binascii.unhexlify(value)) or {}
    except (ValueError, binascii.Error, TypeError):
        # Tolerate a future version storing the JSON directly.
        return load_json(value) or {}


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


@dataclass
class Session:
    """One `chats/<hash>/<agentId>` directory."""

    directory: Path
    id: str
    cwd: str | None
    title: str
    created_at: str | None
    updated_at: str | None

    @property
    def db(self) -> Path:
        return self.directory / "store.db"

    @property
    def prompts_file(self) -> Path:
        return self.directory / "prompt_history.json"


def _read_meta(directory: Path) -> Session | None:
    try:
        parsed = load_json((directory / "meta.json").read_bytes())
    except OSError:
        return None
    if not isinstance(parsed, dict):
        return None
    cwd = parsed.get("cwd")
    return Session(
        directory=directory,
        id=directory.name,
        cwd=cwd if isinstance(cwd, str) and cwd else None,
        title=(parsed.get("title") or "").strip() or "Untitled chat",
        created_at=_ms_to_iso(parsed.get("createdAtMs")),
        updated_at=_ms_to_iso(parsed.get("updatedAtMs")),
    )


def sessions() -> list[Session]:
    root = chats_dir()
    if not root.is_dir():
        return []
    found = []
    for meta in sorted(root.glob("*/*/meta.json")):
        session = _read_meta(meta.parent)
        if session:
            found.append(session)
    return found


def sessions_under(project: Path) -> list[Session]:
    """Sessions whose working directory is `project` or inside it."""
    target = str(Path(project).resolve())
    out = []
    for session in sessions():
        if not session.cwd:
            continue
        cwd = str(Path(session.cwd).resolve())
        if cwd == target or cwd.startswith(target + "/"):
            out.append(session)
    return out


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #


def _truncate(result: str) -> str:
    if len(result) <= MAX_TOOL_RESULT:
        return result
    return (result[:MAX_TOOL_RESULT]
            + f"\n... [{len(result) - MAX_TOOL_RESULT} chars truncated]")


def _attach_results(record: dict, pending: dict[str, ToolCall]) -> None:
    """Fill in the results of calls already recorded on an earlier turn."""
    for block in record.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool-result":
            continue
        call = pending.pop(block.get("toolCallId") or "", None)
        if call is None:
            continue
        # `experimental_content` repeats `result` verbatim; ignore it.
        result = block.get("result")
        if result is not None and not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        if isinstance(result, str):
            call.result = _truncate(result)
        call.status = block.get("status") or ("completed" if call.result else None)


def _is_injected_context(text: str) -> bool:
    """Is this turn Cursor's context preamble rather than something typed?

    In the JSONL transcripts the preamble and the prompt share one message, so
    lifting `<user_query>` out of it is enough. This store splits them into
    separate records, leaving a preamble-only turn — many kilobytes of
    environment, rules and skill listings — that would otherwise be archived as
    if the user had written it.
    """
    return "<user_query>" not in text and "<user_info>" in text


def _turn(record: dict, role: str) -> tuple[list[str], list[str], list[ToolCall],
                                            str | None, dict[str, ToolCall]]:
    content = record.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [], [], [], None, {}

    texts: list[str] = []
    thinking: list[str] = []
    tools: list[ToolCall] = []
    by_call_id: dict[str, ToolCall] = {}
    stamp = None

    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text") or ""
            if role == "user":
                if _is_injected_context(text):
                    continue
                text, stamp = _unwrap_user_text(text)
            if text.strip():
                texts.append(text.strip())
        elif kind == "reasoning":
            reasoning = (block.get("text") or "").strip()
            if reasoning:
                thinking.append(reasoning)
        elif kind == "tool-call":
            name = block.get("toolName") or "unknown"
            args = block.get("args")
            call = ToolCall(
                name=TOOL_ALIASES.get(name, name),
                source_name=name,
                input=args if isinstance(args, dict) else {"raw": args},
            )
            tools.append(call)
            call_id = block.get("toolCallId")
            if isinstance(call_id, str) and call_id:
                by_call_id[call_id] = call

    return texts, thinking, tools, stamp, by_call_id


def _classify(blobs: dict[str, bytes]) -> tuple[dict[str, dict], dict[str, bytes]]:
    """Split the store into message records and the nodes that order them."""
    records: dict[str, dict] = {}
    nodes: dict[str, bytes] = {}
    for blob_id, data in blobs.items():
        parsed = load_json(data)
        if isinstance(parsed, dict) and parsed.get("role"):
            records[blob_id] = parsed
        elif data:
            nodes[blob_id] = data
    return records, nodes


def conversation_refs(records: dict[str, dict], nodes: dict[str, bytes],
                      latest: str = "") -> list[str]:
    """The ordered blob ids of the longest thread in the store.

    Snapshots are cumulative, so "covers the most messages" means "is the whole
    conversation". See the module docstring for why `latest` cannot be trusted on
    its own.
    """
    best: list[str] = []
    best_count = -1
    for blob_id, data in nodes.items():
        refs = blob_refs(data)
        count = sum(1 for ref in refs if ref in records)
        if count > best_count or (count == best_count and blob_id == latest):
            best, best_count = refs, count
    return best


def _messages(records: dict[str, dict],
              refs: list[str]) -> tuple[list[Message], str | None, str | None]:
    messages: list[Message] = []
    pending: dict[str, ToolCall] = {}
    first_stamp = last_stamp = None

    for ref in refs:
        record = records.get(ref)
        if record is None:
            continue
        role = record.get("role")
        if role == "tool":
            _attach_results(record, pending)
            continue
        if role not in ("user", "assistant"):
            continue  # the system prompt is not part of the conversation
        texts, thinking, tools, stamp, by_call_id = _turn(record, role)
        if not texts and not thinking and not tools:
            continue
        pending.update(by_call_id)
        if stamp:
            first_stamp = first_stamp or stamp
            last_stamp = stamp
        messages.append(
            Message(id=f"{len(messages) + 1:05d}", role=role,
                    text="\n\n".join(texts), thinking="\n\n".join(thinking),
                    tools=tools, created_at=stamp)
        )
    return messages, first_stamp, last_stamp


def load_session(session: Session) -> Conversation | None:
    if not session.db.exists():
        return None
    with DBSnapshot(session.db) as snap:
        conn = connect_readonly(snap)
        try:
            meta = _store_meta(conn)
            blobs = {
                (key.hex() if isinstance(key, (bytes, bytearray)) else str(key)): data
                for key, data in conn.execute("SELECT id, data FROM blobs")
            }
        finally:
            conn.close()

    records, nodes = _classify(blobs)
    latest = meta.get("latestRootBlobId")
    refs = conversation_refs(records, nodes,
                             latest if isinstance(latest, str) else "")
    messages, first_stamp, last_stamp = _messages(records, refs)
    if not messages:
        return None

    conv = Conversation(
        id=session.id,
        title=(meta.get("name") or "").strip() or session.title,
        created_at=first_stamp or _ms_to_iso(meta.get("createdAt"))
        or session.created_at,
        updated_at=last_stamp or session.updated_at,
        source="cursor-chats",
        model=meta.get("lastUsedModel"),
        mode=meta.get("mode"),
    )
    conv.messages = messages
    conv.files = sorted({
        str(value)
        for message in messages for tool in message.tools
        for key, value in (tool.input or {}).items()
        if key in _PATH_KEYS and isinstance(value, str)
    })
    return conv


def load_chats(project: Path) -> list[Conversation]:
    out = []
    for session in sessions_under(project):
        conv = load_session(session)
        if conv:
            out.append(conv)
    return out


def prompts(project: Path) -> list[str]:
    """Prompt recall for `project`, oldest first.

    Cursor stores these newest-first per session, the reverse of Claude Code's
    `history.jsonl`, so they are flipped here.
    """
    out: list[str] = []
    for session in sessions_under(project):
        try:
            parsed = load_json(session.prompts_file.read_bytes())
        except OSError:
            continue
        if not isinstance(parsed, list):
            continue
        for item in reversed(parsed):
            if isinstance(item, str) and item.strip():
                out.append(item)
    return out
