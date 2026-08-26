"""Translate persistent instructions between agents.

Agents express standing instructions three ways, and the vocabularies line up
closely enough to translate mechanically:

    always-on prose        Cursor `alwaysApply: true`   Claude `.claude/rules/*.md`
                           `.cursorrules` (legacy)      `CLAUDE.md`
    path-scoped rules      Cursor `globs:`              Claude `paths:` frontmatter
    on-demand procedures   Cursor description-only      Claude skill (`SKILL.md`)

The third mapping is the interesting one: a Cursor rule with a description but no
globs is "agent requested", which is exactly what a skill is. Translating it to a
skill rather than an always-on rule keeps it out of every session's context.

Exclusion does not map as cleanly. Cursor has `.cursorignore`; Claude Code has no
ignore file and instead expresses exclusion as a `permissions.deny` rule. Deny
rules have no negation form, so `!pattern` lines cannot be represented and are
reported instead of silently dropped.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)

#: Frontmatter keys that mean nothing outside their own agent.
AGENT_ONLY_KEYS = {"alwaysApply", "globs", "disable-model-invocation", "type",
                   "paths"}


@dataclass
class Translation:
    written: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def merge(self, other: Translation) -> Translation:
        self.written += other.written
        self.skipped += other.skipped
        self.unsupported += other.unsupported
        self.notes += other.notes
        return self


# --------------------------------------------------------------------------- #
# Front matter
# --------------------------------------------------------------------------- #


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML front-matter reader: flat scalars and `- ` lists only.

    Deliberately not a YAML dependency — rule front matter in practice is a
    handful of scalars, and this keeps the package dependency-free.
    """
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta: dict = {}
    key = None
    for raw in match.group(1).splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(line.lstrip()[2:].strip().strip("\"'"))
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if value in ("", ">-", ">", "|"):
                meta[key] = []
            elif value.lower() in ("true", "false"):
                meta[key] = value.lower() == "true"
            else:
                meta[key] = value.strip("\"'")
    return meta, text[match.end():]


def dump_frontmatter(meta: dict) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines += [f'  - "{item}"' for item in value]
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join([*lines, "---"]) + "\n"


def parse_globs(value) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = re.split(r"[,\s]+", value)
    else:
        return []
    return [g.strip().strip("\"'") for g in items if g and g.strip()]


# --------------------------------------------------------------------------- #
# Cursor -> Claude Code
# --------------------------------------------------------------------------- #


def cursor_rules_to_claude(project: Path, out_dir: Path) -> Translation:
    """`.cursor/rules/**/*.mdc` -> `.claude/rules/*.md` or a skill."""
    result = Translation()
    sources = sorted(set(project.glob(".cursor/rules/**/*.mdc"))
                     | set(project.glob("*/.cursor/rules/**/*.mdc")))
    for path in sources:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.skipped.append(f"{path}: {exc}")
            continue

        meta, body = split_frontmatter(text)
        owner = path.relative_to(project).parts[0]
        prefix = "" if owner == ".cursor" else f"{owner}-"
        name = f"{prefix}{path.stem}"
        description = meta.get("description") or ""
        globs = parse_globs(meta.get("globs"))
        always = bool(meta.get("alwaysApply"))

        if always or (not globs and not description):
            target = out_dir / ".claude" / "rules" / f"{name}.md"
            head = {"description": description} if description else {}
        elif globs:
            target = out_dir / ".claude" / "rules" / f"{name}.md"
            head = {"paths": globs}
            if description:
                head["description"] = description
        else:
            target = out_dir / ".claude" / "skills" / name / "SKILL.md"
            head = {"name": name, "description": description}

        for key in AGENT_ONLY_KEYS:
            meta.pop(key, None)
        for key, value in meta.items():
            head.setdefault(key, value)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_frontmatter(head) + "\n" + body.strip() + "\n",
                          encoding="utf-8")
        result.written.append(target)
    return result


def cursor_legacy_to_claude(project: Path, out_dir: Path) -> Translation:
    """`.cursorrules` -> an appendix to paste into CLAUDE.md."""
    result = Translation()
    for path in sorted(set(project.glob(".cursorrules"))
                       | set(project.glob("*/.cursorrules"))):
        rel = path.relative_to(project)
        owner = rel.parts[0] if len(rel.parts) > 1 else project.name
        target = out_dir / "claude-md-appendix" / f"{owner}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        body = path.read_text(encoding="utf-8", errors="replace").strip()
        target.write_text(f"<!-- migrated from {rel} -->\n\n"
                          f"## Rules carried over ({owner})\n\n{body}\n",
                          encoding="utf-8")
        result.written.append(target)
    return result


def cursorignore_to_deny(project: Path, out_dir: Path) -> Translation:
    """`.cursorignore` -> a `permissions.deny` fragment."""
    result = Translation()
    patterns: list[str] = []
    for name in (".cursorignore", ".cursorindexingignore"):
        for path in sorted(set(project.glob(name))
                           | set(project.glob(f"*/{name}"))):
            base = path.parent.relative_to(project)
            for raw in path.read_text(encoding="utf-8",
                                      errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("!"):
                    result.unsupported.append(f"{path.name}: {line} "
                                              "(deny rules have no negation)")
                    continue
                pattern = line if base == Path(".") else f"{base}/{line.lstrip('/')}"
                patterns.append(f"Read({pattern})")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "settings.deny-fragment.json"
    target.write_text(json.dumps({"permissions": {"deny": sorted(set(patterns))}},
                                 indent=2) + "\n", encoding="utf-8")
    result.written.append(target)
    result.notes.append(f"{len(set(patterns))} deny rule(s); merge into "
                        ".claude/settings.json by hand")
    return result


# --------------------------------------------------------------------------- #
# Claude Code -> Cursor
# --------------------------------------------------------------------------- #


def claude_rules_to_cursor(project: Path, out_dir: Path) -> Translation:
    """`CLAUDE.md` and `.claude/rules/*.md` -> `.cursor/rules/*.mdc`."""
    result = Translation()
    target_dir = out_dir / ".cursor" / "rules"

    for path in sorted(set(project.glob(".claude/rules/**/*.md"))
                       | set(project.glob("*/.claude/rules/**/*.md"))):
        meta, body = split_frontmatter(path.read_text(encoding="utf-8",
                                                      errors="replace"))
        paths = meta.get("paths")
        paths = paths if isinstance(paths, list) else parse_globs(paths)
        head: dict = {"description": meta.get("description") or ""}
        if paths:
            head["globs"] = ",".join(paths)
            head["alwaysApply"] = False
        else:
            head["alwaysApply"] = True
        target = target_dir / f"{path.stem}.mdc"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_frontmatter(head) + "\n" + body.strip() + "\n",
                          encoding="utf-8")
        result.written.append(target)

    for name in ("CLAUDE.md", ".claude/CLAUDE.md"):
        path = project / name
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        # Block-level HTML comments are maintainer notes, stripped before use.
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
        target = target_dir / "project-instructions.mdc"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            dump_frontmatter({"description": "Project instructions carried over "
                                             "from Claude Code",
                              "alwaysApply": True})
            + "\n" + body + "\n", encoding="utf-8")
        result.written.append(target)
        break

    if not result.written:
        result.notes.append("no CLAUDE.md or .claude/rules found")
    return result


def deny_to_cursorignore(project: Path, out_dir: Path) -> Translation:
    """`permissions.deny` `Read(...)` rules -> `.cursorignore` lines."""
    result = Translation()
    patterns: list[str] = []
    for name in (".claude/settings.json", ".claude/settings.local.json"):
        path = project / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except ValueError as exc:
            result.skipped.append(f"{name}: invalid JSON ({exc})")
            continue
        for rule in ((data.get("permissions") or {}).get("deny") or []):
            match = re.match(r"(?:Read|Edit)\((.+)\)$", str(rule))
            if match:
                patterns.append(match.group(1).lstrip("./"))
            else:
                result.unsupported.append(f"{rule} (not a path rule)")

    if not patterns:
        result.notes.append("no Read()/Edit() deny rules to convert")
        return result
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "cursorignore"
    target.write_text("# generated by tool-baton from permissions.deny\n"
                      + "\n".join(sorted(set(patterns))) + "\n",
                      encoding="utf-8")
    result.written.append(target)
    result.notes.append(f"{len(set(patterns))} pattern(s); copy to .cursorignore")
    return result


# --------------------------------------------------------------------------- #
# Shared: skills and MCP
# --------------------------------------------------------------------------- #


def copy_skills(source_root: Path, out_dir: Path, label: str) -> Translation:
    """Skills are portable as-is; `SKILL.md` has the same shape in both agents."""
    result = Translation()
    if not source_root.is_dir():
        return result
    for entry in sorted(p for p in source_root.iterdir() if p.is_dir()):
        source = next((entry / n for n in ("SKILL.md", "SKILLS.md", "skill.md")
                       if (entry / n).is_file()), None)
        if source is None:
            result.skipped.append(f"{entry}: no SKILL.md")
            continue
        meta, body = split_frontmatter(source.read_text(encoding="utf-8",
                                                        errors="replace"))
        meta.setdefault("name", entry.name)
        meta.setdefault("description", f"Migrated from {label} skill {entry.name}.")
        for key in AGENT_ONLY_KEYS:
            meta.pop(key, None)

        target_dir = out_dir / "skills" / entry.name
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "SKILL.md").write_text(
            dump_frontmatter(meta) + "\n" + body.strip() + "\n", encoding="utf-8")
        for extra in entry.rglob("*"):
            if extra.is_dir() or extra == source:
                continue
            dest = target_dir / extra.relative_to(entry)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(extra, dest)
        result.written.append(target_dir / "SKILL.md")
    return result


def copy_mcp(candidates: list[tuple[Path, Path]]) -> Translation:
    """`mcpServers` has the same shape in every agent that supports MCP."""
    result = Translation()
    for source, target in candidates:
        if not source.is_file() or source.stat().st_size == 0:
            continue
        try:
            data = json.loads(source.read_text(encoding="utf-8", errors="replace"))
        except ValueError as exc:
            result.skipped.append(f"{source}: invalid JSON ({exc})")
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not servers:
            result.skipped.append(f"{source}: no mcpServers")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n",
                          encoding="utf-8")
        result.written.append(target)
        result.notes.append(f"{target.name}: {', '.join(sorted(servers))}")
    return result
