"""Retitle sessions Claude Code has already stored.

A session's `summary` record carries a title, but the resume picker renders the
**first user message**, flattened onto one line. A thread whose first turn opens
with a provenance banner therefore displays as the banner, not as its title.
Fixing that means editing the first user turn, and keeping `summary` in step.

Three shapes, chosen with `--mode`:

  title    keep the metadata block under the headline
  compact  collapse the metadata to a single line
  minimal  the headline alone — cleanest picker, least provenance
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

MARKER = "imported by tool-baton"
LEGACY_MARKERS = ("imported from Cursor by cursor-to-claude", "# Imported Cursor chat: ")

_LEADING_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*\n?", re.S)
_LEGACY_HEADING = re.compile(r"^# Imported Cursor chat: (?P<title>.*)$", re.M)
_CONVERTED = re.compile(r"\A\[(?P<tag>[^\]]{1,32})\]\s+(?P<title>.*)$", re.M)
_META_LINE = re.compile(
    r"^- (Composer id|Cursor composer id|Source id|Started|Last active|Source|"
    r"Mode|Cursor mode|Diff): (?P<value>.*)$", re.M)
#: A metadata line a previous `compact` pass produced. Reused rather than
#: dropped, so re-running compact is a no-op instead of degrading the file.
_COMPACT_LINE = re.compile(r"^(?:source|cursor):[0-9a-fA-F]{6,}(?: · .*)?$", re.M)

MODES = ("title", "compact", "minimal")
CAVEAT = ("Replay of a thread from another agent. Tool output is historical, "
          "not the current state of the tree.")


@dataclass
class Result:
    path: Path
    session_id: str
    old_title: str = ""
    new_title: str = ""
    changed: bool = False
    note: str = ""


def find_sessions(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.jsonl")) if directory.is_dir() else []


def read_records(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                # Keep unparseable lines verbatim rather than dropping content.
                records.append({"__raw__": line})
    return records


def write_records(path: Path, records: list[dict], backup: bool = True) -> Path | None:
    made = None
    if backup and path.exists():
        made = path.with_suffix(f".jsonl.bak-{int(time.time())}")
        shutil.copy2(path, made)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            if "__raw__" in record:
                handle.write(record["__raw__"] + "\n")
            else:
                handle.write(json.dumps(record, ensure_ascii=False,
                                        default=str) + "\n")
    tmp.replace(path)
    return made


def _first_user_index(records: list[dict]) -> int | None:
    for index, record in enumerate(records):
        if record.get("type") == "user":
            return index
    return None


def _text_of(record: dict) -> str | None:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text") or ""
    return None


def _set_text(record: dict, text: str) -> None:
    message = record.setdefault("message", {})
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                block["text"] = text
                return
        content.insert(0, {"type": "text", "text": text})
    else:
        message["content"] = [{"type": "text", "text": text}]


def is_imported(records: list[dict], prefixes: tuple[str, ...] = ()) -> bool:
    """Did this package (or its predecessor) write this session?"""
    for record in records[:4]:
        if record.get("type") == "summary":
            summary = str(record.get("summary", ""))
            if any(summary.startswith(p) for p in prefixes) or summary.startswith("["):
                return True
        text = _text_of(record) or ""
        if MARKER in text or any(m in text for m in LEGACY_MARKERS):
            return True
        if _CONVERTED.match(text.strip()):
            return True
    return False


def current_title(records: list[dict]) -> str:
    """Best guess at what the resume picker currently shows."""
    index = _first_user_index(records)
    if index is None:
        for record in records:
            if record.get("type") == "summary":
                return str(record.get("summary", ""))
        return ""
    return " ".join((_text_of(records[index]) or "").split())[:110]


def extract_title(text: str) -> str | None:
    match = _LEGACY_HEADING.search(text)
    if match:
        return match.group("title").strip()
    stripped = _LEADING_COMMENT.sub("", text).strip()
    match = _CONVERTED.match(stripped)
    return match.group("title").strip() if match else None


def _metadata(text: str) -> dict[str, str]:
    return {m.group(1): m.group("value").strip() for m in _META_LINE.finditer(text)}


def build_preamble(title: str, prefix: str, mode: str, text: str) -> str:
    headline = f"{prefix}{title}".rstrip()
    if mode == "minimal":
        return headline

    body = _LEADING_COMMENT.sub("", text)
    body = _LEGACY_HEADING.sub("", body, count=1).strip()
    body = _CONVERTED.sub("", body, count=1).strip()

    if mode == "compact":
        meta = _metadata(text)
        if meta:
            bits = []
            source_id = meta.get("Composer id") or meta.get("Cursor composer id") \
                or meta.get("Source id")
            if source_id:
                bits.append("source:" + source_id.strip("`")[:8])
            started = meta.get("Started", "")[:10]
            last = meta.get("Last active", "")[:10]
            if started and last and started != last:
                bits.append(f"{started} → {last}")
            elif started or last:
                bits.append(started or last)
            for key in ("Mode", "Cursor mode", "Diff"):
                if meta.get(key):
                    bits.append(meta[key])
                    break
            files = len(re.findall(r"^- `/", body, re.M))
            if files:
                bits.append(f"{files} files")
            line = " · ".join(bits)
        else:
            existing = _COMPACT_LINE.search(body)
            line = existing.group(0).strip() if existing else ""
        return f"{headline}\n\n{line}\n\n{CAVEAT}" if line else f"{headline}\n\n{CAVEAT}"

    return f"{headline}\n\n{body}" if body else headline


def retitle_file(path: Path, prefix: str = "[baton] ", mode: str = "title",
                 new_title: str | None = None, only_imported: bool = True) -> Result:
    records = read_records(path)
    session_id = next((r.get("sessionId") for r in records if r.get("sessionId")),
                      path.stem)
    result = Result(path=path, session_id=str(session_id))

    if not records:
        result.note = "empty file"
        return result
    if only_imported and new_title is None and not is_imported(records, (prefix,)):
        result.note = "not an imported session"
        return result

    index = _first_user_index(records)
    if index is None:
        result.note = "no user turn to retitle"
        return result
    text = _text_of(records[index])
    if text is None:
        result.note = "first user turn has no text block"
        return result

    result.old_title = current_title(records)
    title = new_title or extract_title(text)
    if not title:
        summary = next((str(r.get("summary", "")) for r in records
                        if r.get("type") == "summary"), "")
        title = re.sub(r"^\[[^\]]{1,32}\]\s*", "", summary).strip()
        if not title:
            title = " ".join(text.split())[:70]

    replacement = build_preamble(title, prefix, mode, text)
    if replacement.strip() != text.strip():
        _set_text(records[index], replacement)
        result.changed = True

    wanted = f"{prefix}{title}".rstrip()
    summary_index = next((i for i, r in enumerate(records)
                          if r.get("type") == "summary"), None)
    if summary_index is None:
        leaf = next((r.get("uuid") for r in reversed(records) if r.get("uuid")), None)
        if leaf:
            records.insert(0, {"type": "summary", "summary": wanted,
                               "leafUuid": leaf})
            result.changed = True
    elif records[summary_index].get("summary") != wanted:
        records[summary_index]["summary"] = wanted
        result.changed = True

    result.new_title = " ".join(replacement.split())[:110]
    if result.changed:
        result.records = records  # type: ignore[attr-defined]
    else:
        result.note = "already up to date"
    return result


def retitle_dir(directory: Path, prefix: str = "[baton] ", mode: str = "title",
                renames: dict[str, str] | None = None, apply: bool = False,
                only_imported: bool = True) -> list[Result]:
    renames = renames or {}
    results = []
    for path in find_sessions(directory):
        explicit = renames.get(path.stem)
        result = retitle_file(path, prefix=prefix, mode=mode, new_title=explicit,
                              only_imported=only_imported and explicit is None)
        if result.changed and apply:
            write_records(path, result.records, backup=True)
        results.append(result)
    return results


def list_titles(directory: Path, prefix: str = "[baton] ") -> list[Result]:
    out = []
    for path in find_sessions(directory):
        records = read_records(path)
        session_id = next((r.get("sessionId") for r in records
                           if r.get("sessionId")), path.stem)
        out.append(Result(path=path, session_id=str(session_id),
                          old_title=current_title(records),
                          note="imported" if is_imported(records, (prefix,))
                          else "native"))
    return out
