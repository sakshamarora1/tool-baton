"""Read SQLite databases that belong to a running application, safely."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path


def load_json(value):
    """Parse a JSON blob out of a key-value table, tolerating junk."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


class DBSnapshot:
    """Copy a SQLite file (and any -wal/-shm sidecars) somewhere disposable.

    The editor whose database we are reading may be running and holding write
    locks. Working from a copy means we can never corrupt the original and never
    observe a half-applied transaction. This is the only way this package ever
    touches another application's database.
    """

    def __init__(self, src: Path):
        self.src = Path(src)
        self._tmp: str | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        if not self.src.exists():
            raise FileNotFoundError(f"no such database: {self.src}")
        self._tmp = tempfile.mkdtemp(prefix="toolbaton-")
        dest = Path(self._tmp) / self.src.name
        shutil.copy2(self.src, dest)
        for suffix in ("-wal", "-shm"):
            side = self.src.with_name(self.src.name + suffix)
            if side.exists():
                shutil.copy2(side, dest.with_name(dest.name + suffix))
        self.path = dest
        return dest

    def __exit__(self, *exc) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)


def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
