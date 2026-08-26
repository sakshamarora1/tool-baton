"""Claude Code's prompt-recall buffer.

`~/.claude/history.jsonl` is what the up arrow reads. One JSON object per line:

    {"display": ..., "pastedContents": {}, "timestamp": <ms>,
     "project": "/abs/path", "sessionId": ...}

Carrying prompt history across is a small feature with an outsized effect: it is
the difference between a fresh install and one that already remembers how you
phrase things.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .paths import history_file


def build(prompts: list[str], project: Path,
          session_id: str = "baton-import") -> list[dict]:
    """Shape prompts into history records.

    Source agents generally store prompts newest-first with no timestamps, so
    these are emitted oldest-first with synthetic timestamps one second apart
    ending now — which makes the up-arrow order match the order they were typed.
    """
    now = int(time.time() * 1000)
    ordered = list(reversed(prompts))
    return [
        {"display": prompt, "pastedContents": {},
         "timestamp": now - (len(ordered) - index) * 1000,
         "project": str(project), "sessionId": session_id}
        for index, prompt in enumerate(ordered)
    ]


def write_fragment(records: list[dict], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "history.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def install(fragment: Path) -> dict:
    """Append a fragment to the live history, backing it up first.

    Records already present (same prompt and project) are skipped, so running the
    install twice does not double the buffer.
    """
    target = history_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: set[tuple[str, str]] = set()
    backup = None

    if target.exists():
        backup = target.with_suffix(f".jsonl.bak-{int(time.time())}")
        shutil.copy2(target, backup)
        with target.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                existing.add((record.get("display", ""), record.get("project", "")))

    added = 0
    with target.open("a", encoding="utf-8") as out, \
            fragment.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if (record.get("display", ""), record.get("project", "")) in existing:
                continue
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            added += 1

    return {"target": str(target), "added": added,
            "backup": str(backup) if backup else None}
