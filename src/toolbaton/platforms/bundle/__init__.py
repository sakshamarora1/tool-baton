"""IR bundle adapter — a file, not an agent.

`bundle.json` is the serialised intermediate representation. Treating it as a
platform makes the IR a first-class endpoint rather than an implementation
detail, which buys three things:

  * migrate off a machine you no longer have. Export a bundle from the old
    laptop, carry the file, write it into whatever agent you use now.
  * migrate into an agent that did not exist when the bundle was made, since the
    bundle is independent of both endpoints.
  * develop and test an adapter without a real installation of the source agent.

`--source` names the file. With `--source auto` (the default) it looks for
`bundle.json` in the build directory for `--project`, which is where `migrate`
and `export` leave one.
"""

from __future__ import annotations

from pathlib import Path

from ... import ir
from ...ir import Conversation
from ...util.paths import default_output_dir
from ..base import READ, WRITE, Platform, ReadOptions, WriteOptions, WriteResult

FILENAME = "bundle.json"


class Bundle(Platform):
    name = "bundle"
    label = "IR bundle"
    capabilities = (READ, WRITE)
    verified = True
    caveat = f"a file, not an agent; --source names it, else <out>/{FILENAME}"

    def detect(self) -> bool:
        # Always usable: it needs no installed agent, only a file.
        return True

    def _resolve(self, options: ReadOptions) -> Path:
        if options.source and options.source != "auto":
            path = Path(options.source).expanduser()
            return path / FILENAME if path.is_dir() else path
        return default_output_dir(options.project) / FILENAME

    def describe(self, project: Path) -> list[str]:
        candidate = default_output_dir(project) / FILENAME
        state = "present" if candidate.is_file() else "not built yet"
        return [f"  default bundle   {candidate}  [{state}]",
                "  pass --source <path> to read a bundle from anywhere"]

    def read(self, options: ReadOptions) -> list[Conversation]:
        self.require(READ)
        path = self._resolve(options)
        if not path.is_file():
            raise FileNotFoundError(
                f"no bundle at {path}. Create one with "
                f"`baton export --format ir`, or pass --source <path>."
            )
        return ir.read_bundle(path)

    def write(self, conversations: list[Conversation],
              options: WriteOptions) -> WriteResult:
        self.require(WRITE)
        path = ir.write_bundle(conversations, options.project, "bundle",
                               options.out_dir)
        return WriteResult(files=[path],
                           notes=["replay it with `baton migrate --from bundle "
                                  f"--source {path}`"])

    def install(self, out_dir: Path, project: Path, apply: bool) -> WriteResult:
        # A bundle has no live location to install into; it is already a file.
        return WriteResult(notes=[f"bundle stays at {out_dir / FILENAME}"])


PLATFORM = Bundle()
