"""Windows Subsystem for Linux, and the one thing it moves.

Under WSL an agent installed *inside* the distribution keeps its files where any
Linux install would (`~/.claude`, `~/.cursor`), so those adapters need no help.
What moves is a Windows-hosted editor: Cursor running on the Windows side keeps
its user data on the Windows volume, reachable only through the drive mounts.

Every `/mnt`-shaped assumption in this package lives in this module, so no
adapter has to learn about it. Standard library only, like the rest of the
package.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

#: `\\wsl.localhost\<distro>\...` and the older `\\wsl$\<distro>\...`, as they
#: appear in the authority of a `file://` URI. The distribution is then the
#: first path segment rather than part of the authority.
UNC_HOSTS = frozenset({"wsl.localhost", "wsl$"})

#: Windows keeps template and service profiles beside real ones.
_NOT_A_USER = frozenset({"default", "default user", "public", "all users"})

_DRIVE = re.compile(r"^([A-Za-z]):[\\/]?(.*)$")

_CACHE: dict[str, list[Path]] = {}


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    if not sys.platform.startswith("linux"):
        return False
    try:
        release = Path("/proc/version").read_text(errors="replace")
    except OSError:
        return False
    return "microsoft" in release.lower()


def distro() -> str | None:
    """The distribution we are running in, if it identified itself."""
    return os.environ.get("WSL_DISTRO_NAME") or None


def is_this_distro(name: str | None) -> bool:
    """Does `name` refer to the distribution whose filesystem we are on?

    A path under another distribution is not reachable from here, so treating it
    as local would attribute a conversation to a project that does not exist.
    An unknown local name is treated as a match: the alternative is discarding
    real paths, and the comparison is case-insensitive because Cursor writes the
    name lowercased (`wsl+ubuntu`) while WSL reports it capitalised (`Ubuntu`).
    """
    if not name:
        return False
    mine = distro()
    return True if not mine else name.strip().lower() == mine.strip().lower()


def mount_root() -> Path:
    """Where Windows drives are mounted — `/mnt` unless `wsl.conf` says otherwise."""
    try:
        text = Path("/etc/wsl.conf").read_text(errors="replace")
    except OSError:
        return Path("/mnt")
    section = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
        elif section == "automount" and "=" in line:
            key, _, value = line.partition("=")
            if key.strip().lower() == "root" and value.strip():
                return Path(value.strip().rstrip("/") or "/")
    return Path("/mnt")


def win_to_wsl(path: str) -> Path | None:
    """`C:\\Users\\you` -> `/mnt/c/Users/you`."""
    match = _DRIVE.match(str(path).strip())
    if not match:
        return None
    drive, rest = match.group(1).lower(), match.group(2).replace("\\", "/")
    return mount_root() / drive / rest.lstrip("/")


def _run(command: list[str], cwd: str | None) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=10, check=False, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    out = result.stdout.replace("\r", "").strip()
    return out or None


def _profiles_from_mounts() -> list[Path]:
    """Windows user profiles visible through the drive mounts.

    Cheap and side-effect free, which is why it is tried before interop.
    """
    root = mount_root()
    found: list[Path] = []
    try:
        drives = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return found
    for drive in drives:
        users = drive / "Users"
        try:
            entries = sorted(users.iterdir()) if users.is_dir() else []
        except OSError:
            continue
        for entry in entries:
            if entry.name.lower() in _NOT_A_USER:
                continue
            if (entry / "AppData" / "Roaming").is_dir():
                found.append(entry)
    return found


def _profile_from_interop() -> Path | None:
    """Ask Windows itself, for when the mounts hold more than one profile.

    Spawning a Windows process is the only way to learn which profile belongs to
    the current user. Interop can be switched off, so this must be allowed to
    fail. `cmd.exe` warns and relocates when started from a Linux-only cwd, so
    it is run from the mount root.
    """
    root = mount_root()
    cwd = str(root) if root.is_dir() else None
    raw = _run(["cmd.exe", "/c", "echo %USERPROFILE%"], cwd)
    if not raw or "%USERPROFILE%" in raw:
        return None
    # `wslpath` honours a custom automount root; fall back to translating here.
    translated = _run(["wslpath", "-u", raw], cwd) or str(win_to_wsl(raw) or "")
    path = Path(translated) if translated else None
    return path if path and path.is_dir() else None


def _discover() -> list[Path]:
    found = _profiles_from_mounts()
    if len(found) == 1:
        return found
    current = _profile_from_interop()
    if current:
        return [current] + [p for p in found if p != current]
    return found


def windows_homes() -> list[Path]:
    """Candidate Windows user profiles, best guess first.

    A list rather than one answer because a machine can have several Windows
    users and only one of them installed the editor; the caller decides by
    looking for a file it expects to find.
    """
    override = os.environ.get("BATON_WINDOWS_HOME")
    if override:
        return [Path(override).expanduser()]
    if not is_wsl():
        return []
    if "homes" not in _CACHE:
        _CACHE["homes"] = _discover()
    return list(_CACHE["homes"])


def windows_home() -> Path | None:
    homes = windows_homes()
    return homes[0] if homes else None


def app_data_roaming(app_name: str) -> list[Path]:
    """Where a Windows-hosted Electron editor keeps its user data, seen from here."""
    return [home / "AppData" / "Roaming" / app_name for home in windows_homes()]
