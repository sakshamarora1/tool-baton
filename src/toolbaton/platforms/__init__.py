"""The platform registry.

Adapters are looked up by name on the command line (`--from`, `--to`). Imports
are lazy so that a broken or platform-specific adapter cannot stop the CLI from
starting, and so `baton platforms` stays fast.

Agents listed as detect-only are recognised but have no adapter. They are here
deliberately rather than omitted: a user whose agent is unsupported should be
told so, and pointed at `baton probe` and the adapter-authoring skill, instead of
being left to guess why their agent is missing.
"""

from __future__ import annotations

from .base import DetectOnly, Platform

#: name -> "module:attribute", imported on first use.
_IMPLEMENTED = {
    "cursor": "toolbaton.platforms.cursor:PLATFORM",
    "claude-code": "toolbaton.platforms.claude_code:PLATFORM",
}

#: Endpoints that are files rather than agents.
_SINKS = {
    "markdown": "toolbaton.platforms.markdown:PLATFORM",
    "bundle": "toolbaton.platforms.bundle:PLATFORM",
}


class _DetectOnlyPlatform(DetectOnly):
    def __init__(self, name: str, label: str, paths: tuple[str, ...]):
        self.name = name
        self.label = label
        self.paths = paths


#: Agents we can spot but not yet migrate. Paths are the conventional locations;
#: presence proves installation, not that any history exists there.
_DETECT_ONLY = (
    _DetectOnlyPlatform("windsurf", "Windsurf",
                        ("~/.codeium/windsurf",
                         "~/Library/Application Support/Windsurf")),
    _DetectOnlyPlatform("copilot", "GitHub Copilot",
                        ("~/.copilot",
                         "~/Library/Application Support/Code/User/"
                         "globalStorage/github.copilot-chat")),
    _DetectOnlyPlatform("codex", "OpenAI Codex", ("~/.codex",)),
    _DetectOnlyPlatform("cline", "Cline", ("~/.cline",)),
    _DetectOnlyPlatform("continue", "Continue", ("~/.continue",)),
    _DetectOnlyPlatform("gemini", "Gemini CLI", ("~/.gemini",)),
    _DetectOnlyPlatform("qwen", "Qwen Code", ("~/.qwen",)),
    _DetectOnlyPlatform("zed", "Zed",
                        ("~/.config/zed", "~/Library/Application Support/Zed")),
)

_cache: dict[str, Platform] = {}


def _load(spec: str) -> Platform:
    from importlib import import_module

    module_name, _, attr = spec.partition(":")
    return getattr(import_module(module_name), attr)


def names() -> list[str]:
    """Every name accepted by `--from` / `--to`, implemented ones first."""
    return list(_IMPLEMENTED) + list(_SINKS) + [p.name for p in _DETECT_ONLY]


def get(name: str) -> Platform:
    """Resolve a platform by name, raising a helpful error if unknown."""
    key = (name or "").strip().lower()
    aliases = {"claude": "claude-code", "claudecode": "claude-code",
               "cc": "claude-code", "md": "markdown"}
    key = aliases.get(key, key)

    if key in _cache:
        return _cache[key]

    spec = _IMPLEMENTED.get(key) or _SINKS.get(key)
    if spec:
        platform = _load(spec)
        _cache[key] = platform
        return platform

    for platform in _DETECT_ONLY:
        if platform.name == key:
            _cache[key] = platform
            return platform

    raise KeyError(
        f"unknown platform {name!r}. Known: {', '.join(names())}"
    )


def all_platforms() -> list[Platform]:
    """Every registered platform, implemented ones first."""
    out: list[Platform] = []
    for key in list(_IMPLEMENTED) + list(_SINKS):
        try:
            out.append(get(key))
        except Exception:  # an adapter that cannot import must not break the CLI
            continue
    out.extend(_DETECT_ONLY)
    return out


def installed() -> list[Platform]:
    return [p for p in all_platforms() if p.detect()]
