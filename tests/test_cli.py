"""CLI smoke tests.

These exist to catch wiring mistakes — a subcommand that cannot parse its own
arguments, or a default that writes into the working directory.
"""

from __future__ import annotations

import json

import pytest

from toolbaton import cli


def run(argv) -> int:
    return cli.main(argv)


def test_every_subcommand_parses_help(capsys):
    parser = cli.build_parser()
    for name in ("platforms", "doctor", "inventory", "migrate", "install",
                 "export", "retitle", "probe", "clean"):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args([name, "--help"])
        assert exc.value.code == 0


def test_platforms_lists_both_verified_adapters(capsys):
    assert run(["platforms"]) == 0
    out = capsys.readouterr().out
    assert "cursor" in out and "claude-code" in out
    # Unsupported agents must be visible rather than silently missing.
    assert "no adapter yet" in out or "detection only" in out


def test_unknown_platform_fails_with_a_useful_message(project, capsys):
    with pytest.raises(SystemExit):
        run(["inventory", "--project", str(project), "--from", "nonsense"])
    assert "unknown platform" in capsys.readouterr().err


def test_migrate_writes_only_under_out(cursor_env, claude_env, tmp_path, capsys):
    out = tmp_path / "explicit-out"
    assert run(["migrate", "--project", str(cursor_env["project"]),
                "--from", "cursor", "--to", "claude-code",
                "--source", "sqlite", "--out", str(out)]) == 0
    assert (out / "sessions").is_dir()
    assert (out / "archive" / "INDEX.md").is_file()
    assert (out / "bundle.json").is_file()


def test_install_is_a_dry_run_without_yes(cursor_env, claude_env, tmp_path):
    out = tmp_path / "out"
    run(["migrate", "--project", str(cursor_env["project"]), "--from", "cursor",
         "--to", "claude-code", "--source", "sqlite", "--out", str(out)])
    sessions = claude_env["sessions"]
    run(["install", "--project", str(cursor_env["project"]),
         "--to", "claude-code", "--out", str(out)])
    assert not sessions.exists() or not list(sessions.glob("*.jsonl"))


def test_install_with_yes_copies_sessions(cursor_env, claude_env, tmp_path):
    out = tmp_path / "out"
    run(["migrate", "--project", str(cursor_env["project"]), "--from", "cursor",
         "--to", "claude-code", "--source", "sqlite", "--out", str(out)])
    run(["install", "--project", str(cursor_env["project"]),
         "--to", "claude-code", "--out", str(out), "--yes"])
    assert list(claude_env["sessions"].glob("*.jsonl"))


def test_export_ir_bundle_is_reloadable(cursor_env, tmp_path):
    out = tmp_path / "out"
    assert run(["export", "--project", str(cursor_env["project"]),
                "--from", "cursor", "--source", "sqlite",
                "--format", "ir", "--out", str(out)]) == 0
    payload = json.loads((out / "bundle.json").read_text())
    assert payload["schema"] == 1
    assert payload["conversations"]


def test_redaction_is_on_by_default(cursor_env, tmp_path):
    out = tmp_path / "out"
    run(["migrate", "--project", str(cursor_env["project"]), "--from", "cursor",
         "--to", "markdown", "--source", "sqlite", "--out", str(out)])
    body = "\n".join(p.read_text() for p in (out / "archive").glob("*.md"))
    # The fixture plants a key in a tool result.
    assert "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in body
    assert "[redacted]" in body


def test_no_redact_opts_out(cursor_env, tmp_path):
    out = tmp_path / "out"
    run(["migrate", "--project", str(cursor_env["project"]), "--from", "cursor",
         "--to", "markdown", "--source", "sqlite", "--out", str(out),
         "--no-redact"])
    body = "\n".join(p.read_text() for p in (out / "archive").glob("*.md"))
    assert "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" in body


def test_filters_narrow_the_selection(cursor_env, tmp_path, capsys):
    project = str(cursor_env["project"])
    run(["inventory", "--project", project, "--from", "cursor",
         "--source", "sqlite", "--min-messages", "99"])
    assert "no conversations matched" in capsys.readouterr().out


def test_clean_needs_yes(tmp_path, project, capsys):
    out = tmp_path / "out"
    (out / "sessions").mkdir(parents=True)
    (out / "sessions" / "x.jsonl").write_text("{}\n")
    run(["clean", "--project", str(project), "--out", str(out)])
    assert out.is_dir()
    run(["clean", "--project", str(project), "--out", str(out), "--yes"])
    assert not out.exists()
