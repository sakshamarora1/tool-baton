"""The adapter contract.

A platform is one coding agent. It declares what it can do, how to tell whether
it is installed, and — depending on its capabilities — how to read its history
into the IR and how to write the IR back out.

Capabilities are declared rather than inferred so `baton platforms` can print an
honest matrix. An adapter that has never been run against real data says so;
claiming support we cannot demonstrate is worse than admitting the gap.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

# Capability tokens.
READ = "read"          # can turn its own storage into IR conversations
WRITE = "write"        # can turn IR conversations into something it consumes
RULES = "rules"        # can translate persistent instructions / ignore files
PROMPTS = "prompts"    # can read or write the prompt-recall history
RETITLE = "retitle"    # can rename threads it has already written

ALL_CAPABILITIES = (READ, WRITE, RULES, PROMPTS, RETITLE)


class Unsupported(RuntimeError):
    """Raised when an adapter is asked for a capability it does not declare."""


@dataclass
class ReadOptions:
    """Everything that narrows or shapes a read."""

    project: Path
    source: str = "auto"           # adapter-specific sub-source selector
    since: str = ""                # ISO date floor, e.g. "2026-01"
    limit: int = 0                 # keep the N most recently active
    min_messages: int = 2


@dataclass
class WriteOptions:
    """Everything that shapes a write."""

    project: Path
    out_dir: Path
    tools: str = "text"            # text | blocks | drop
    prefix: str = "[baton] "       # title prefix for imported threads
    mode: str = "title"            # title | compact | minimal
    include_thinking: bool = True
    redact: bool = True
    target_version: str | None = None


@dataclass
class WriteResult:
    files: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    redactions: int = 0

    def extend(self, other: WriteResult) -> WriteResult:
        self.files.extend(other.files)
        self.notes.extend(other.notes)
        self.redactions += other.redactions
        return self


class Platform(ABC):
    """One coding agent."""

    #: short identifier used on the command line, e.g. "claude-code"
    name: str = ""
    #: human-readable name for output
    label: str = ""
    #: declared capabilities; anything absent raises `Unsupported`
    capabilities: tuple[str, ...] = ()
    #: has this adapter been exercised against real data from this agent?
    verified: bool = False
    #: shown by `baton platforms` when a capability is missing or partial
    caveat: str = ""

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def require(self, capability: str) -> None:
        if not self.supports(capability):
            raise Unsupported(
                f"{self.label} cannot {capability}. "
                f"Run `baton platforms` to see what it does support."
            )

    @abstractmethod
    def detect(self) -> bool:
        """Is this agent installed on this machine?"""

    def describe(self, project: Path) -> list[str]:
        """Lines for `baton doctor` — where this agent's data lives, and how much."""
        return []

    # -- capability entry points ------------------------------------------- #

    def read(self, options: ReadOptions):
        self.require(READ)
        raise NotImplementedError

    def write(self, conversations, options: WriteOptions) -> WriteResult:
        self.require(WRITE)
        raise NotImplementedError

    def install(self, out_dir: Path, project: Path, apply: bool) -> WriteResult:
        """Copy built output into this agent's live locations."""
        self.require(WRITE)
        raise NotImplementedError


class DetectOnly(Platform):
    """An agent we can recognise but cannot yet migrate.

    These exist so a user whose agent is unsupported gets told that plainly,
    along with a pointer to `baton probe` and the adapter-authoring skill, rather
    than silence.
    """

    paths: tuple[str, ...] = ()

    capabilities = ()
    verified = False
    caveat = "detection only — no adapter yet; see `baton probe`"

    def detect(self) -> bool:
        return any(Path(p).expanduser().exists() for p in self.paths)

    def describe(self, project: Path) -> list[str]:
        found = [p for p in self.paths if Path(p).expanduser().exists()]
        return [f"  found at {Path(p).expanduser()}" for p in found]
