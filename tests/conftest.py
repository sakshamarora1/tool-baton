from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.fixtures import build


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n")
    (root / "README.md").write_text(
        "# demo\n\nA small demo project used by the test suite for fixtures.\n")
    return root


@pytest.fixture
def cursor_env(tmp_path: Path, project: Path, monkeypatch) -> dict:
    """A complete fake Cursor installation."""
    app_support = tmp_path / "CursorAppSupport" / "User"
    cursor_home = tmp_path / "dot-cursor"
    build.build_cursor_db(app_support, project)
    build.build_cursor_workspace(app_support, project)

    monkeypatch.setenv("CURSOR_APP_SUPPORT",
                       str(tmp_path / "CursorAppSupport"))
    monkeypatch.setenv("CURSOR_HOME", str(cursor_home))
    return {"app_support": app_support, "cursor_home": cursor_home,
            "project": project}


@pytest.fixture
def claude_env(tmp_path: Path, project: Path, monkeypatch) -> dict:
    """A fake Claude Code config directory."""
    home = tmp_path / "dot-claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    from toolbaton.platforms.claude_code.paths import project_dir
    return {"home": home, "project": project, "sessions": project_dir(project)}
