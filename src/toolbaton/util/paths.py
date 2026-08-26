"""Filesystem conventions shared by every platform adapter."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from . import wsl

_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_DRIVE_PREFIXED = re.compile(r"^/[A-Za-z]:[\\/]")


def _app_support_candidates(app_name: str) -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / app_name]
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        return [Path(base) / app_name]
    # Under WSL the editor may be the Windows build, whose user data sits on the
    # Windows volume. A native Linux build can also be present, so both are
    # candidates rather than one being chosen by platform alone.
    return [home / ".config" / app_name, *wsl.app_data_roaming(app_name)]


def electron_app_support(app_name: str, env_override: str | None = None,
                         marker: str | None = None) -> Path:
    """User-data directory for a VS Code-derived (Electron) editor.

    Cursor, VS Code, Windsurf and friends all follow the same per-OS layout, so
    every adapter for one of them can share this.

    `marker` is a path the caller expects to find inside the right directory. It
    exists because under WSL there can be two plausible answers — a native Linux
    install and a Windows one — and only one of them holds any history. Picking
    the directory that actually contains the data beats picking by platform and
    reporting "missing".
    """
    if env_override:
        value = os.environ.get(env_override)
        if value:
            return Path(value).expanduser()
    candidates = _app_support_candidates(app_name)
    if marker:
        for candidate in candidates:
            if (candidate / marker).exists():
                return candidate
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


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


def _plain_path(text: str) -> str | None:
    """A path with no scheme, in whichever spelling an editor stored it."""
    if _DRIVE.match(text):
        return (str(wsl.win_to_wsl(text)) if wsl.is_wsl()
                else text.replace("\\", "/"))
    if text.startswith("/"):
        # `file:///c%3A/Users/you` decodes to `/c:/Users/you`.
        if _DRIVE_PREFIXED.match(text):
            return _plain_path(text[1:])
        return text
    if "\\" in text:
        # Seen in Cursor's store: a POSIX path written with Windows separators.
        converted = text.replace("\\", "/")
        return converted if converted.startswith("/") else None
    return None


def uri_to_path(uri) -> str | None:
    """Turn one of an editor's path spellings into a path reachable from here.

    `file:///Users/you/x.py` -> `/Users/you/x.py`. Under WSL an editor running on
    the Windows side records the same file three other ways, all of which have to
    resolve to the Linux path for a conversation to be attributed to a project:

    >>> uri_to_path("vscode-remote://wsl%2Bubuntu/home/you/repo")   # doctest: +SKIP
    '/home/you/repo'
    >>> uri_to_path("file://wsl.localhost/Ubuntu/home/you/repo")    # doctest: +SKIP
    '/home/you/repo'
    >>> uri_to_path("file://wsl%24/Ubuntu/home/you/repo")           # doctest: +SKIP
    '/home/you/repo'

    Anything naming a location *not* reachable from here — another distribution,
    a remote host, an `https` URL — is `None`. That matters more than it looks:
    an authority that is parsed as part of the path yields a relative path, which
    then resolves against the working directory and silently attributes another
    project's history to this one.
    """
    if not isinstance(uri, str):
        return None
    text = uri.strip()
    if not text:
        return None

    scheme, separator, rest = text.partition("://")
    if not separator:
        return _plain_path(text)

    authority, _, tail = rest.partition("/")
    authority = unquote(authority)
    path = f"/{tail}"

    if not authority:
        return _plain_path(unquote(path)) if scheme.lower() == "file" else None

    if authority.lower().startswith("wsl+"):
        name, remainder = authority[4:], path
    elif authority.lower() in wsl.UNC_HOSTS:
        name, _, subpath = tail.partition("/")
        name, remainder = unquote(name), f"/{subpath}"
    else:
        return None

    if remainder == "/" or not wsl.is_this_distro(name):
        return None
    return _plain_path(unquote(remainder))


def default_output_dir(project: Path, tool: str = "tool-baton") -> Path:
    """Where builds go when `--out` is not given.

    Deliberately deterministic rather than a fresh `mkdtemp`: `migrate` and
    `install` are separate processes, and `install` has to be able to find what
    `migrate` produced. Keyed on the project so two projects never collide.
    """
    import tempfile

    return Path(tempfile.gettempdir()) / tool / slugify_path(project)
