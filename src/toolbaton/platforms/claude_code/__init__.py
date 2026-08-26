"""Claude Code adapter."""

from __future__ import annotations

import shutil
from pathlib import Path

from ... import rules as rules_mod
from ...ir import Conversation
from ..base import (
    PROMPTS,
    READ,
    RETITLE,
    RULES,
    WRITE,
    Platform,
    ReadOptions,
    WriteOptions,
    WriteResult,
)
from . import history, paths, reader, writer


class ClaudeCode(Platform):
    name = "claude-code"
    label = "Claude Code"
    capabilities = (READ, WRITE, RULES, PROMPTS, RETITLE)
    verified = True

    def detect(self) -> bool:
        return paths.claude_home().is_dir() or shutil.which("claude") is not None

    def describe(self, project: Path) -> list[str]:
        home = paths.claude_home()
        sessions = paths.project_dir(project)
        count = len(list(sessions.glob("*.jsonl"))) if sessions.is_dir() else 0
        version = paths.detect_version() or "not on PATH"
        lines = [f"  config dir       {home}",
                 f"  cli version      {version}",
                 f"  project sessions {sessions}  [{count} file(s)]"]
        hist = paths.history_file()
        if hist.is_file():
            records = sum(1 for _ in hist.open(errors="replace"))
            lines.append(f"  prompt history   {hist}  [{records} record(s)]")
        return lines

    # -- read --------------------------------------------------------------- #

    def read(self, options: ReadOptions) -> list[Conversation]:
        self.require(READ)
        return reader.read_project(options.project)

    def read_prompts(self, project: Path) -> list[str]:
        return reader.read_prompt_history(project)

    # -- write -------------------------------------------------------------- #

    def write(self, conversations: list[Conversation],
              options: WriteOptions) -> WriteResult:
        self.require(WRITE)
        return writer.write(conversations, options)

    def write_prompts(self, prompts: list[str], options: WriteOptions) -> WriteResult:
        result = WriteResult()
        if not prompts:
            return result
        fragment = history.write_fragment(
            history.build(prompts, options.project), options.out_dir / "history")
        result.files.append(fragment)
        result.notes.append(f"{len(prompts)} prompt(s) for ~/.claude/history.jsonl")
        return result

    def write_rules(self, source_project: Path, options: WriteOptions) -> WriteResult:
        """Translate a source agent's rules into Claude Code's layout."""
        out = options.out_dir / "config"
        translation = rules_mod.cursor_rules_to_claude(source_project, out)
        translation.merge(rules_mod.cursor_legacy_to_claude(source_project, out))
        translation.merge(rules_mod.cursorignore_to_deny(source_project, out))
        result = WriteResult(files=list(translation.written),
                             notes=list(translation.notes))
        result.notes += [f"unsupported: {u}" for u in translation.unsupported]
        return result

    # -- install ------------------------------------------------------------ #

    def install_targets(self, out_dir: Path,
                        project: Path) -> dict[str, tuple[Path, Path]]:
        return {
            "sessions": (out_dir / "sessions", paths.project_dir(project)),
            "archive": (out_dir / "archive", project / "docs" / "agent-archive"),
            "knowledge": (out_dir / "knowledge", project / ".claude" / "context"),
            "rules": (out_dir / "config" / ".claude" / "rules",
                      project / ".claude" / "rules"),
            "skills-project": (out_dir / "config" / ".claude" / "skills",
                               project / ".claude" / "skills"),
            "skills-user": (out_dir / "config" / "skills", paths.skills_dir()),
        }

    def install(self, out_dir: Path, project: Path, apply: bool) -> WriteResult:
        self.require(WRITE)
        result = WriteResult()
        for _label, (src, dest) in self.install_targets(out_dir, project).items():
            if not src.is_dir():
                continue
            for path in sorted(p for p in src.rglob("*") if p.is_file()):
                target = dest / path.relative_to(src)
                result.files.append(target)
                if apply:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)

        fragment = out_dir / "history" / "history.jsonl"
        if fragment.is_file():
            if apply:
                outcome = history.install(fragment)
                result.notes.append(
                    f"history: appended {outcome['added']} record(s); "
                    f"backup {outcome['backup']}")
            else:
                count = sum(1 for _ in fragment.open(errors="replace"))
                result.notes.append(
                    f"history: would append {count} record(s) to "
                    f"{paths.history_file()} (backup taken first)")
        return result

    # -- retitle ------------------------------------------------------------ #

    def session_dir(self, project: Path) -> Path:
        return paths.project_dir(project)


PLATFORM = ClaudeCode()
