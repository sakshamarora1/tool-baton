"""Filesystem conventions shared by every platform adapter."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote


def electron_app_support(app_name: str, env_override: str | None = None) -> Path:
    """User-data directory for a VS Code-derived (Electron) editor.

    Cursor, VS Code, Windsurf and friends all follow the same per-OS layout, so
    every adapter for one of them can share this.
    """
    if env_override:
        value = os.environ.get(env_override)
        if value:
            return Path(value).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / app_name
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        return Path(base) / app_name
    return home / ".config" / app_name


def slugify_path(path: Path | str, keep_leading: bool = False) -> str:
    """Rewrite a filesystem path into a flat directory name.

    Both Cursor and Claude Code name their per-project directories by replacing
    every run of non-alphanumeric characters with a single hyphen. They differ
    only in whether the leading separator survives:

    >>> slugify_path("/Users/you/code/myrepo")
    'Users-you-code-myrepo'
    >>> slugify_path("/Users/you/code/myrepo", keep_leading=True)
    '-Users-you-code-myrepo'
    """
    raw = str(Path(path).resolve())
    slug = re.sub(r"[^A-Za-z0-9]+", "-", raw)
    return slug if keep_leading else slug.strip("-")


def uri_to_path(uri) -> str | None:
    """`file:///Users/you/x.py` -> `/Users/you/x.py`; ignore non-file URIs."""
    if not isinstance(uri, str):
        return None
    if uri.startswith("file://"):
        return unquote(uri[len("file://"):])
    if uri.startswith("/"):
        return uri
    return None


def default_output_dir(project: Path, tool: str = "tool-baton") -> Path:
    """Where builds go when `--out` is not given.

    Deliberately deterministic rather than a fresh `mkdtemp`: `migrate` and
    `install` are separate processes, and `install` has to be able to find what
    `migrate` produced. Keyed on the project so two projects never collide.
    """
    import tempfile

    return Path(tempfile.gettempdir()) / tool / slugify_path(project)
