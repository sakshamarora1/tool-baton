"""WSL detection and Windows-profile discovery.

Under WSL a Windows-hosted editor's data is only reachable through the drive
mounts, so getting this wrong means reporting "missing" for history that is
sitting on disk.
"""

from __future__ import annotations

from pathlib import Path

from toolbaton.util import wsl


def _profile(root: Path, drive: str, user: str) -> Path:
    home = root / drive / "Users" / user
    (home / "AppData" / "Roaming").mkdir(parents=True)
    return home


def test_not_wsl_when_nothing_says_so(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(wsl.sys, "platform", "darwin")
    assert wsl.is_wsl() is False
    assert wsl.windows_homes() == []


def test_distro_env_alone_proves_wsl(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert wsl.is_wsl() is True
    assert wsl.distro() == "Ubuntu"


def test_distro_comparison_is_case_insensitive(monkeypatch):
    # Cursor writes `wsl+ubuntu`; WSL reports `Ubuntu`.
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert wsl.is_this_distro("ubuntu") is True
    assert wsl.is_this_distro("Ubuntu") is True
    assert wsl.is_this_distro("Debian") is False
    assert wsl.is_this_distro("") is False


def test_unknown_local_distro_accepts_any_name(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    assert wsl.is_this_distro("ubuntu") is True


def test_env_override_wins_and_skips_discovery(monkeypatch, tmp_path):
    monkeypatch.setenv("BATON_WINDOWS_HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(wsl, "_discover", lambda: [Path("/should/not/be/used")])
    assert wsl.windows_homes() == [tmp_path / "elsewhere"]


def test_single_profile_is_found_without_interop(monkeypatch, tmp_path):
    home = _profile(tmp_path, "c", "you")
    monkeypatch.setattr(wsl, "mount_root", lambda: tmp_path)

    def refuse():
        raise AssertionError("interop must not be needed for one profile")

    monkeypatch.setattr(wsl, "_profile_from_interop", refuse)
    assert wsl._discover() == [home]


def test_template_profiles_are_ignored(monkeypatch, tmp_path):
    _profile(tmp_path, "c", "Default")
    _profile(tmp_path, "c", "Public")
    real = _profile(tmp_path, "c", "you")
    monkeypatch.setattr(wsl, "mount_root", lambda: tmp_path)
    assert wsl._profiles_from_mounts() == [real]


def test_interop_breaks_a_tie_between_profiles(monkeypatch, tmp_path):
    first = _profile(tmp_path, "c", "aaa")
    mine = _profile(tmp_path, "c", "zzz")
    monkeypatch.setattr(wsl, "mount_root", lambda: tmp_path)
    monkeypatch.setattr(wsl, "_profile_from_interop", lambda: mine)
    assert wsl._discover() == [mine, first]


def test_tie_without_interop_keeps_both_candidates(monkeypatch, tmp_path):
    # Interop can be switched off; the caller disambiguates by marker instead.
    first = _profile(tmp_path, "c", "aaa")
    second = _profile(tmp_path, "d", "bbb")
    monkeypatch.setattr(wsl, "mount_root", lambda: tmp_path)
    monkeypatch.setattr(wsl, "_profile_from_interop", lambda: None)
    assert wsl._discover() == [first, second]


def test_win_to_wsl_maps_drive_letters(monkeypatch):
    monkeypatch.setattr(wsl, "mount_root", lambda: Path("/mnt"))
    assert wsl.win_to_wsl(r"C:\Users\you") == Path("/mnt/c/Users/you")
    assert wsl.win_to_wsl("D:/data") == Path("/mnt/d/data")
    assert wsl.win_to_wsl("/home/you") is None


def test_win_to_wsl_honours_a_custom_mount_root(monkeypatch, tmp_path):
    monkeypatch.setattr(wsl, "mount_root", lambda: tmp_path / "drives")
    assert wsl.win_to_wsl(r"C:\x") == tmp_path / "drives" / "c" / "x"


def test_mount_root_reads_automount_override(monkeypatch, tmp_path):
    conf = tmp_path / "wsl.conf"
    conf.write_text("[boot]\nsystemd=true\n\n[automount]\nroot = /drives/\n")
    real = Path.read_text

    def fake(self, *args, **kwargs):
        return real(conf, *args, **kwargs) if str(self) == "/etc/wsl.conf" \
            else real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake)
    assert wsl.mount_root() == Path("/drives")
