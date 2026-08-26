"""Path and slug conventions.

These rules decide whether a written session is discoverable at all, and the two
agents differ in a single character, so they are worth pinning down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbaton.util import wsl
from toolbaton.util.paths import (
    default_output_dir,
    electron_app_support,
    slugify_path,
    uri_to_path,
)


@pytest.fixture
def in_ubuntu(monkeypatch):
    """Pretend we are running inside a WSL distribution named Ubuntu."""
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(wsl, "mount_root", lambda: Path("/mnt"))


def test_cursor_and_claude_slugs_differ_only_by_leading_separator():
    path = "/Users/you/code/myrepo"
    assert slugify_path(path) == "Users-you-code-myrepo"
    assert slugify_path(path, keep_leading=True) == "-Users-you-code-myrepo"


def test_slug_collapses_every_non_alphanumeric_run():
    assert slugify_path("/a/b_c/d.e-f") == "a-b-c-d-e-f"


def test_uri_to_path_handles_file_uris_and_percent_encoding():
    assert uri_to_path("file:///tmp/a%20b.py") == "/tmp/a b.py"
    assert uri_to_path("/tmp/plain.py") == "/tmp/plain.py"
    assert uri_to_path("https://example.com") is None
    assert uri_to_path(None) is None


@pytest.mark.parametrize("uri", [
    "vscode-remote://wsl%2Bubuntu/home/you/code/myrepo",
    "file://wsl.localhost/Ubuntu/home/you/code/myrepo",
    "file://wsl%24/Ubuntu/home/you/code/myrepo",
])
def test_every_wsl_spelling_reaches_the_same_linux_path(uri, in_ubuntu):
    # A Windows-hosted Cursor records the one file all three ways; each has to
    # resolve or the conversation is attributed to no project at all.
    assert uri_to_path(uri) == "/home/you/code/myrepo"


def test_another_distribution_is_not_our_filesystem(in_ubuntu):
    assert uri_to_path("vscode-remote://wsl%2Bdebian/home/you/code/myrepo") is None
    assert uri_to_path("file://wsl.localhost/Debian/home/you/code/myrepo") is None


def test_unreachable_authority_is_dropped_not_read_as_a_relative_path(in_ubuntu):
    # Regression: stripping a fixed `file://` prefix left `host/rest`, which
    # resolved against the cwd and landed *under* the project being migrated,
    # attributing another project's history to it.
    for uri in ("file://somehost/Ubuntu/home/you/code/myrepo",
                "vscode-remote://ssh-remote%2Bbox/home/you/code/myrepo",
                "https://example.com/x"):
        got = uri_to_path(uri)
        assert got is None or got.startswith("/"), got


def test_a_distribution_root_names_no_file(in_ubuntu):
    assert uri_to_path("file://wsl.localhost/Ubuntu") is None
    assert uri_to_path("vscode-remote://wsl%2Bubuntu/") is None


def test_windows_separators_on_a_linux_path(in_ubuntu):
    assert uri_to_path(r"\home\you\code\myrepo\a.py") == "/home/you/code/myrepo/a.py"


def test_drive_letters_resolve_through_the_mounts(in_ubuntu):
    assert uri_to_path(r"C:\Users\you\a.txt") == "/mnt/c/Users/you/a.txt"
    assert uri_to_path("file:///c%3A/Users/you/a.txt") == "/mnt/c/Users/you/a.txt"


def test_marker_picks_the_candidate_holding_the_data(monkeypatch, tmp_path):
    # Under WSL a native Linux directory and a Windows one can both exist while
    # only one has any history in it.
    empty = tmp_path / "linux" / "Cursor"
    real = tmp_path / "windows" / "Cursor"
    empty.mkdir(parents=True)
    marker = real / "User" / "globalStorage" / "state.vscdb"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"")
    monkeypatch.setattr("toolbaton.util.paths._app_support_candidates",
                        lambda app: [empty, real])
    assert electron_app_support("Cursor",
                                marker="User/globalStorage/state.vscdb") == real
    # With no marker to go on, the first directory that exists still wins.
    assert electron_app_support("Cursor") == empty


def test_default_output_dir_is_deterministic_and_outside_cwd(tmp_path):
    # `migrate` and `install` are separate processes; a random mkdtemp would
    # leave install unable to find the build.
    first = default_output_dir(tmp_path)
    second = default_output_dir(tmp_path)
    assert first == second
    assert Path.cwd() not in first.parents
