"""Markdown output — a destination, not an agent."""

from __future__ import annotations

from pathlib import Path

from ..base import WRITE, Platform, WriteOptions, WriteResult
from . import writer


class Markdown(Platform):
    name = "markdown"
    label = "Markdown archive"
    capabilities = (WRITE,)
    verified = True
    caveat = "output only; every agent can read files, so this always works"

    def detect(self) -> bool:
        return True

    def describe(self, project: Path) -> list[str]:
        return [f"  archive target   {project / 'docs' / 'agent-archive'}",
                "  always available — a destination, not an installed agent"]

    def write(self, conversations, options: WriteOptions) -> WriteResult:
        return writer.write(conversations, options)

    def install(self, out_dir: Path, project: Path, apply: bool) -> WriteResult:
        import shutil

        result = WriteResult()
        src = out_dir / "archive"
        dest = project / "docs" / "agent-archive"
        if not src.is_dir():
            return result
        for path in sorted(p for p in src.rglob("*") if p.is_file()):
            target = dest / path.relative_to(src)
            result.files.append(target)
            if apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        return result


PLATFORM = Markdown()
