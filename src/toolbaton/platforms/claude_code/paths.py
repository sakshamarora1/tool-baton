"""Where Claude Code keeps its state."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ...util.paths import slugify_path

#: Used only as a fallback when the CLI is not on PATH. Stamped into the
#: `version` field of emitted records; never authoritative.
FALLBACK_VERSION = "2.1.0"


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR",
                               Path.home() / ".claude")).expanduser()


def project_slug(project: Path) -> str:
    """Claude Code keeps the leading separator: `/Users/you/x` -> `-Users-you-x`."""
    return slugify_path(project, keep_leading=True)


def project_dir(project: Path) -> Path:
    return claude_home() / "projects" / project_slug(project)


def history_file() -> Path:
    """The prompt-recall buffer behind the up arrow."""
    return claude_home() / "history.jsonl"


def skills_dir() -> Path:
    return claude_home() / "skills"


def memory_dir(project: Path) -> Path:
    return project_dir(project) / "memory"


def detect_version() -> str | None:
    """Ask the installed CLI for its version.

    Emitted sessions carry a `version` field. Hardcoding the author's local
    version into other people's files would be wrong, so this is detected at
    runtime and overridable by the caller.
    """
    try:
        result = subprocess.run(["claude", "--version"], capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip().split()
    return token[0] if token else None
