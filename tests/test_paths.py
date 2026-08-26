"""Path and slug conventions.

These rules decide whether a written session is discoverable at all, and the two
agents differ in a single character, so they are worth pinning down.
"""

from __future__ import annotations

from pathlib import Path

from toolbaton.util.paths import default_output_dir, slugify_path, uri_to_path


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


def test_default_output_dir_is_deterministic_and_outside_cwd(tmp_path):
    # `migrate` and `install` are separate processes; a random mkdtemp would
    # leave install unable to find the build.
    first = default_output_dir(tmp_path)
    second = default_output_dir(tmp_path)
    assert first == second
    assert Path.cwd() not in first.parents
