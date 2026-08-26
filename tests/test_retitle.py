"""Retitling already-written sessions.

The idempotency cases here are regressions. `compact` originally degraded on a
second pass: it read metadata from bullets that its own first pass had already
collapsed, found none, and silently dropped the metadata line.
"""

from __future__ import annotations

import json

from toolbaton.platforms.claude_code import retitle as rt

LEGACY_BANNER = (
    "<!-- imported from Cursor by cursor-to-claude; not a real Claude Code session -->\n"
    "# Imported Cursor chat: Understanding the scan API\n"
    "\n"
    "- Cursor composer id: `1922328d-bca2-4a39-8c0d-7f1106e9d2be`\n"
    "- Started: 2025-10-20T15:26:43.255Z\n"
    "- Last active: 2025-10-22T14:47:16.335Z\n"
    "- Source: sqlite\n"
    "- Cursor mode: chat\n"
    "\n"
    "Files this thread touched:\n"
    "- `/tmp/project/src/app.py`\n"
)


def _session(tmp_path, text=LEGACY_BANNER, summary=None):
    path = tmp_path / "1922328d-bca2-4a39-8c0d-7f1106e9d2be.jsonl"
    records = []
    if summary:
        records.append({"type": "summary", "summary": summary, "leafUuid": "a1"})
    records += [
        {"type": "user", "uuid": "u1", "sessionId": path.stem,
         "message": {"role": "user", "content": [{"type": "text", "text": text}]}},
        {"type": "assistant", "uuid": "a1", "sessionId": path.stem,
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "reply"}]}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _first_turn(path):
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") == "user":
            return record["message"]["content"][0]["text"]
    return ""


def _summary(path):
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") == "summary":
            return record["summary"]
    return None


def test_legacy_banner_becomes_a_prefixed_title(tmp_path):
    path = _session(tmp_path)
    results = rt.retitle_dir(tmp_path, apply=True)
    assert results[0].changed
    text = _first_turn(path)
    assert text.startswith("[baton] Understanding the scan API")
    assert "<!--" not in text
    assert "# Imported Cursor chat" not in text


def test_summary_is_created_and_kept_in_step(tmp_path):
    path = _session(tmp_path)
    rt.retitle_dir(tmp_path, apply=True)
    assert _summary(path) == "[baton] Understanding the scan API"


def test_minimal_mode_leaves_only_the_title(tmp_path):
    path = _session(tmp_path)
    rt.retitle_dir(tmp_path, mode="minimal", apply=True)
    assert _first_turn(path) == "[baton] Understanding the scan API"


def test_compact_mode_keeps_one_metadata_line(tmp_path):
    path = _session(tmp_path)
    rt.retitle_dir(tmp_path, mode="compact", apply=True)
    text = _first_turn(path)
    assert text.startswith("[baton] Understanding the scan API")
    assert "source:1922328d" in text
    assert "2025-10-20 → 2025-10-22" in text


def test_compact_is_idempotent(tmp_path):
    # Regression: the second pass used to drop the metadata line entirely.
    path = _session(tmp_path)
    rt.retitle_dir(tmp_path, mode="compact", apply=True)
    first = _first_turn(path)
    second_run = rt.retitle_dir(tmp_path, mode="compact", apply=True)
    assert not any(r.changed for r in second_run)
    assert _first_turn(path) == first


def test_every_mode_is_idempotent(tmp_path):
    for mode in rt.MODES:
        directory = tmp_path / mode
        directory.mkdir()
        path = _session(directory)
        rt.retitle_dir(directory, mode=mode, apply=True)
        before = _first_turn(path)
        again = rt.retitle_dir(directory, mode=mode, apply=True)
        assert not any(r.changed for r in again), mode
        assert _first_turn(path) == before, mode


def test_prefixes_do_not_stack(tmp_path):
    path = _session(tmp_path)
    for _ in range(3):
        rt.retitle_dir(tmp_path, mode="minimal", apply=True)
    assert _first_turn(path).count("[baton]") == 1


def test_native_sessions_are_left_alone(tmp_path):
    path = _session(tmp_path, text="Just an ordinary question I typed myself")
    results = rt.retitle_dir(tmp_path, apply=True)
    assert not results[0].changed
    assert results[0].note == "not an imported session"
    assert _first_turn(path) == "Just an ordinary question I typed myself"


def test_all_flag_reaches_native_sessions(tmp_path):
    path = _session(tmp_path, text="Ordinary question")
    rt.retitle_dir(tmp_path, mode="minimal", apply=True, only_imported=False)
    assert _first_turn(path).startswith("[baton] ")


def test_explicit_rename_overrides_the_derived_title(tmp_path):
    path = _session(tmp_path)
    rt.retitle_dir(tmp_path, mode="minimal",
                   renames={path.stem: "Author list perf"}, apply=True)
    assert _first_turn(path) == "[baton] Author list perf"
    assert _summary(path) == "[baton] Author list perf"


def test_a_backup_is_written_before_any_change(tmp_path):
    _session(tmp_path)
    rt.retitle_dir(tmp_path, apply=True)
    assert list(tmp_path.glob("*.jsonl.bak-*"))


def test_dry_run_writes_nothing(tmp_path):
    path = _session(tmp_path)
    before = path.read_text()
    results = rt.retitle_dir(tmp_path, apply=False)
    assert results[0].changed          # it reports what it would do
    assert path.read_text() == before  # but changes nothing
    assert not list(tmp_path.glob("*.bak-*"))


def test_unparseable_lines_are_preserved(tmp_path):
    path = _session(tmp_path)
    with path.open("a") as handle:
        handle.write("this is not json\n")
    rt.retitle_dir(tmp_path, apply=True)
    assert "this is not json" in path.read_text()


def test_listing_classifies_imported_and_native(tmp_path):
    _session(tmp_path)
    _session(tmp_path / "native") if (tmp_path / "native").mkdir() else None
    rows = rt.list_titles(tmp_path)
    assert rows[0].note == "imported"
