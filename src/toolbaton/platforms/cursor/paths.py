"""Where Cursor keeps its state, and how it names projects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ...util.db import load_json
from ...util.paths import electron_app_support, slugify_path, uri_to_path


def app_support() -> Path:
    return electron_app_support("Cursor", env_override="CURSOR_APP_SUPPORT")


def user_dir() -> Path:
    return app_support() / "User"


def global_db() -> Path:
    """Chat history for *every* workspace lives in this one file."""
    return user_dir() / "globalStorage" / "state.vscdb"


def workspace_storage() -> Path:
    return user_dir() / "workspaceStorage"


def cursor_home() -> Path:
    """`~/.cursor` — rules, skills, plans, and per-project agent transcripts."""
    return Path(os.environ.get("CURSOR_HOME", Path.home() / ".cursor")).expanduser()


def project_slug(project: Path) -> str:
    """Cursor drops the leading separator: `/Users/you/x` -> `Users-you-x`."""
    return slugify_path(project, keep_leading=False)


def project_dir(project: Path) -> Path:
    return cursor_home() / "projects" / project_slug(project)


def transcripts_dir(project: Path) -> Path:
    return project_dir(project) / "agent-transcripts"


def rules_dir(project: Path) -> Path:
    return Path(project) / ".cursor" / "rules"


@dataclass
class Workspace:
    """One `workspaceStorage/<md5>` directory."""

    hash: str
    db: Path
    folder: str | None

    @property
    def name(self) -> str:
        return Path(self.folder).name if self.folder else self.hash


def workspaces() -> list[Workspace]:
    root = workspace_storage()
    found: list[Workspace] = []
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        db = entry / "state.vscdb"
        if not db.exists():
            continue
        folder = None
        meta = entry / "workspace.json"
        if meta.exists():
            parsed = load_json(meta.read_bytes()) or {}
            if isinstance(parsed, dict):
                folder = uri_to_path(parsed.get("folder") or "")
        found.append(Workspace(hash=entry.name, db=db, folder=folder))
    return found


def workspaces_under(root: Path) -> list[Workspace]:
    """Workspaces for `root`, any ancestor, and any subfolder.

    Ancestors count because a workspace opened on the parent directory very often
    contains prompts about this project.
    """
    target = str(Path(root).resolve())
    out = []
    for ws in workspaces():
        if not ws.folder:
            continue
        folder = str(Path(ws.folder).resolve())
        if (folder == target
                or folder.startswith(target + "/")
                or target.startswith(folder + "/")):
            out.append(ws)
    return out
