"""Keep this machine's private data out of the published package.

This tool is developed by reading real chat history, and its output directory
sits next to the source. Documentation discipline is not enough — this test walks
every git-tracked file and fails on anything that identifies a real person or
their projects. It runs in CI, so private data cannot be reintroduced later.
"""

from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Paths whose *content* is exempt: this file names the patterns it forbids.
EXEMPT = {"tests/test_no_private_data.py"}

#: Lines containing any of these are the package's *declared* public identity —
#: author email and repository URL — which a published package must carry. They
#: are not private data, so they are exempt from the username check.
PUBLIC_IDENTITY = ("github.com/", "@gmail.com", "Homepage", "Issues",
                   "Changelog", "authors =")

#: Substrings that must never appear in a tracked file.
DENY = (
    "Desktop/projects",     # this machine's checkout root
    "cds-rdm",              # real project names encountered while developing
    "invenio-rdm",
    "InvenioRDM",
    "cern.ch",
)

TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".cfg", ".txt",
                 ".ini", ".sh", ".gitignore", ""}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                             capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git unavailable")
    if out.returncode != 0:  # pragma: no cover
        pytest.skip("not a git repository")
    return [ROOT / line for line in out.stdout.splitlines() if line.strip()]


def readable_files() -> list[Path]:
    return [p for p in tracked_files()
            if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES
            and str(p.relative_to(ROOT)) not in EXEMPT]


def test_there_are_tracked_files_to_check():
    # Guards against the check silently passing because it found nothing.
    assert readable_files(), "no tracked text files found"


def test_no_real_username_appears():
    username = os.environ.get("USER") or getpass.getuser()
    if not username or len(username) < 3:
        pytest.skip("no usable username to check for")
    offenders = []
    for path in readable_files():
        for line in path.read_text(encoding="utf-8",
                                   errors="replace").splitlines():
            if username not in line:
                continue
            if any(token in line for token in PUBLIC_IDENTITY):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: {line.strip()[:90]}")
    assert not offenders, (
        f"the username {username!r} appears outside declared package identity:\n"
        + "\n".join(offenders)
        + "\nReplace it with a neutral placeholder such as /Users/you/code/myrepo.")


def test_no_home_directory_paths():
    offenders = []
    for path in readable_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for prefix in ("/Users/", "/home/", "C:\\Users\\"):
            for line in text.splitlines():
                if prefix not in line:
                    continue
                # Neutral placeholders are fine; real names are not.
                if any(token in line for token in ("/Users/you", "/Users/me",
                                                   "/home/you", "/home/user",
                                                   "C:\\Users\\you")):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {line.strip()[:90]}")
    assert not offenders, "concrete home paths found:\n" + "\n".join(offenders)


def test_no_denylisted_project_names():
    offenders = []
    for path in readable_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in DENY:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert not offenders, "private references found:\n" + "\n".join(offenders)


def test_migration_output_is_not_tracked():
    offenders = [str(p.relative_to(ROOT)) for p in tracked_files()
                 if "-out/" in str(p) or p.name.startswith("bundle.json")]
    assert not offenders, f"migration output is tracked: {offenders}"
