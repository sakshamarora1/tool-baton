"""Cursor adapter.

Reads at high fidelity: Cursor keeps everything, in two places at different
quality, and both are used. Writes conservatively: only into locations Cursor
already reads as files. This package never writes to Cursor's database — see
`writer.py` for why that line is drawn where it is.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ... import ir as ir_mod
from ... import rules as rules_mod
from ...ir import Conversation
from ..base import (
    PROMPTS,
    READ,
    RULES,
    WRITE,
    Platform,
    ReadOptions,
    WriteOptions,
    WriteResult,
)
from . import paths, reader, transcripts, writer


class Cursor(Platform):
    name = "cursor"
    label = "Cursor"
    capabilities = (READ, WRITE, RULES, PROMPTS)
    verified = True
    caveat = ("writes are file-based only; migrated threads are @-mentionable "
              "but do not appear in Cursor's history sidebar")

    def detect(self) -> bool:
        return paths.global_db().exists() or paths.cursor_home().is_dir()

    def describe(self, project: Path) -> list[str]:
        db = paths.global_db()
        size = f"{db.stat().st_size / 1e6:.0f} MB" if db.exists() else "missing"
        home = paths.cursor_home()
        plans = home / "plans"
        n_transcripts = len(transcripts.transcript_files(project))
        n_plans = len(list(plans.glob("*.md"))) if plans.is_dir() else 0
        skills = "present" if (home / "skills").is_dir() else "none"
        lines = [f"  global database  {db}  [{size}]",
                 f"  workspaceStorage {paths.workspace_storage()}",
                 f"  project dir      {paths.project_dir(project)}",
                 f"  transcripts      {n_transcripts} file(s)",
                 f"  skills           {skills}",
                 f"  plans            {n_plans} file(s)"]
        for workspace in paths.workspaces_under(project):
            lines.append(f"  workspace        {workspace.hash}  {workspace.folder}")
        return lines

    # -- read --------------------------------------------------------------- #

    def read(self, options: ReadOptions) -> list[Conversation]:
        """Read both stores and merge.

        The SQLite store has every thread but a noisier message stream; the JSONL
        transcripts are cleaner but cover only recent ones. `auto` takes the
        cleaner bodies where they exist and the richer metadata always.
        """
        self.require(READ)
        from_sqlite: list[Conversation] = []
        from_transcripts: list[Conversation] = []

        if options.source in ("auto", "transcripts"):
            from_transcripts = transcripts.load_transcripts(options.project)

        if options.source in ("auto", "sqlite"):
            if paths.global_db().exists():
                with reader.CursorStore() as store:
                    for conv in store.conversations():
                        if conv.touches(options.project):
                            from_sqlite.append(store.load(conv))
            elif options.source == "sqlite":
                raise FileNotFoundError(
                    f"Cursor database not found at {paths.global_db()}")

        return ir_mod.merge_sources(from_sqlite, from_transcripts)

    def read_prompts(self, project: Path) -> list[str]:
        return reader.prompts(project) if paths.global_db().exists() else []

    # -- write -------------------------------------------------------------- #

    def write(self, conversations: list[Conversation],
              options: WriteOptions) -> WriteResult:
        self.require(WRITE)
        return writer.write(conversations, options)

    def write_prompts(self, prompts: list[str], options: WriteOptions) -> WriteResult:
        """Cursor has no writable prompt-history file, so this is a no-op."""
        result = WriteResult()
        if prompts:
            result.notes.append(
                f"{len(prompts)} prompt(s) not transferred — Cursor's "
                "aiService.prompts lives in its database, which we do not write")
        return result

    def write_rules(self, source_project: Path, options: WriteOptions) -> WriteResult:
        out = options.out_dir / "config"
        translation = rules_mod.claude_rules_to_cursor(source_project, out)
        translation.merge(rules_mod.deny_to_cursorignore(source_project, out))
        result = WriteResult(files=list(translation.written),
                            notes=list(translation.notes))
        result.notes += [f"unsupported: {u}" for u in translation.unsupported]
        return result

    # -- install ------------------------------------------------------------ #

    def install_targets(self, out_dir: Path,
                        project: Path) -> dict[str, tuple[Path, Path]]:
        return {
            "transcripts": (out_dir / "cursor-transcripts",
                            paths.transcripts_dir(project)),
            "archive": (out_dir / "archive", project / "docs" / "agent-archive"),
            "knowledge": (out_dir / "knowledge", project / ".cursor" / "context"),
            "rules": (out_dir / "config" / ".cursor" / "rules",
                      paths.rules_dir(project)),
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

        fragment = out_dir / "config" / "cursorignore"
        if fragment.is_file():
            result.notes.append(
                f"merge {fragment} into {project / '.cursorignore'} by hand")
        return result


PLATFORM = Cursor()
