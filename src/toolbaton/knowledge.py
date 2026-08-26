"""Rebuild the derived knowledge that no agent exports.

Some agents maintain a vector index of the repository. There is nothing portable
in it, and nothing worth porting: an index is a cache of the tree, and the
destination agent will build its own or read the tree directly.

What *is* portable is the signal that index accumulated indirectly — which files
were actually opened, attached and edited, how often, and how recently. That
ranking is the useful residue of months of work, and it converts cleanly into a
repository map plus a seed instruction file. It is derived purely from
`Conversation.files`, so it works for every adapter for free.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from .ir import Conversation

SKIP_DIR_PARTS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".tox", "site-packages", ".eggs", ".next",
    "target", "vendor",
}


def _relevant(path: str, root: Path) -> bool:
    prefix = str(root.resolve())
    if not (path == prefix or path.startswith(prefix + "/")):
        return False
    return not any(part in SKIP_DIR_PARTS for part in Path(path).parts)


def hot_files(conversations: list[Conversation], root: Path,
              top: int = 120) -> list[tuple[str, int, str]]:
    """Files ranked by how many conversations touched them."""
    counts: Counter[str] = Counter()
    latest: dict[str, str] = {}
    resolved = root.resolve()
    for conv in conversations:
        stamp = (conv.updated_at or conv.created_at or "")[:7]
        for path in conv.files:
            if not _relevant(path, root):
                continue
            rel = str(Path(path).relative_to(resolved))
            counts[rel] += 1
            if stamp > latest.get(rel, ""):
                latest[rel] = stamp
    return [(p, n, latest.get(p, "")) for p, n in counts.most_common(top)]


def hot_modules(conversations: list[Conversation], root: Path,
                top: int = 40) -> list[tuple[str, int]]:
    """The same ranking rolled up to the first path segment."""
    counts: Counter[str] = Counter()
    resolved = root.resolve()
    for conv in conversations:
        seen = set()
        for path in conv.files:
            if not _relevant(path, root):
                continue
            parts = Path(path).relative_to(resolved).parts
            if parts:
                seen.add(parts[0])
        counts.update(seen)
    return counts.most_common(top)


# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                             text=True, timeout=15, check=False)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _readme_summary(repo: Path, limit: int = 220) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        path = repo / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if (not line or len(line) < 30
                    or line.startswith(("#", "=", "-", "..", "|", "!", "[", ":"))):
                continue
            return re.sub(r"\s+", " ", line)[:limit]
    return ""


def repo_map(root: Path) -> list[dict]:
    """One entry per immediate subdirectory that is its own git repository.

    Useful mainly for the multi-repo workspaces where a single agent session
    spans many checkouts — exactly the case where a destination agent has the
    least chance of working the layout out on its own.
    """
    out = []
    for entry in sorted(p for p in root.iterdir() if p.is_dir()):
        if entry.name.startswith(".") or entry.name in SKIP_DIR_PARTS:
            continue
        if not (entry / ".git").exists():
            continue
        stack = []
        if (entry / "pyproject.toml").exists() or (entry / "setup.py").exists():
            stack.append("python")
        if (entry / "package.json").exists():
            stack.append("node")
        if (entry / "Dockerfile").exists() or (entry / "docker-compose.yml").exists():
            stack.append("docker")
        if (entry / "Cargo.toml").exists():
            stack.append("rust")
        if (entry / "go.mod").exists():
            stack.append("go")
        out.append({
            "name": entry.name,
            "remote": _git(entry, "config", "--get", "remote.origin.url"),
            "branch": _git(entry, "rev-parse", "--abbrev-ref", "HEAD"),
            "last_commit": _git(entry, "log", "-1", "--format=%ad|%s",
                                "--date=short"),
            "stack": stack,
            "summary": _readme_summary(entry),
        })
    return out


# --------------------------------------------------------------------------- #


def write_repo_map(entries: list[dict], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Repository map", "",
             f"{len(entries)} git repositor{'y' if len(entries) == 1 else 'ies'} "
             "checked out under this workspace root.", "",
             "| Module | Branch | Stack | What it is |", "|---|---|---|---|"]
    for entry in entries:
        summary = (entry["summary"] or "").replace("|", "\\|")
        lines.append(f"| `{entry['name']}` | {entry['branch'] or '?'} | "
                     f"{', '.join(entry['stack']) or '-'} | {summary} |")
    lines += ["", "## Remotes", ""]
    lines += [f"- `{e['name']}` → {e['remote'] or '(no origin)'}" for e in entries]
    path = out_dir / "REPO_MAP.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_hot_files(files, modules, out_dir: Path, total: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Where the work actually happened", "",
             f"Derived from {total} conversation(s). This is the portable part of "
             "an agent's index: not embeddings, but the record of which files "
             "were returned to again and again.", "",
             "## Modules by conversation count", "",
             "| Module | Conversations |", "|---|---|"]
    lines += [f"| `{name}` | {count} |" for name, count in modules]
    lines += ["", "## Files by conversation count", "",
              "| File | Conversations | Last touched |", "|---|---|---|"]
    lines += [f"| `{p}` | {n} | {w or '?'} |" for p, n, w in files]
    path = out_dir / "HOT_FILES.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_prompt_themes(prompts: list[str], out_dir: Path, top: int = 60) -> Path:
    """A crude frequency read on what the user kept asking for."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stop = {
        "the", "a", "an", "and", "or", "to", "of", "in", "is", "it", "for", "this",
        "that", "with", "on", "be", "are", "you", "we", "not", "can", "do", "if",
        "as", "so", "but", "at", "by", "from", "my", "me", "there", "then", "have",
        "has", "was", "will", "should", "would", "what", "why", "how", "when",
        "which", "also", "just", "like", "make", "need", "want", "use", "using",
        "get", "add", "now", "all", "any", "its", "our", "let", "see", "one",
    }
    words: Counter[str] = Counter()
    for prompt in prompts:
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}", prompt.lower()):
            if word not in stop:
                words[word] += 1
    lines = ["# What you asked for", "",
             f"{len(prompts)} prompt(s) recovered.", "",
             "## Most frequent terms", ""]
    lines += [f"- {word} — {count}" for word, count in words.most_common(top)]
    lines += ["", "## Longest prompts (usually the ones worth keeping)", ""]
    for prompt in sorted(prompts, key=len, reverse=True)[:25]:
        lines += ["> " + " ".join(prompt.split())[:600], ""]
    path = out_dir / "PROMPT_THEMES.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_seed_instructions(root: Path, entries: list[dict], modules,
                            out_dir: Path, filename: str = "CLAUDE.md.draft",
                            archive_hint: str = "docs/agent-archive/") -> Path:
    """A starting instruction file. Deliberately short, explicitly a draft.

    Instruction files are loaded into every session, so anything the agent could
    derive by reading the tree is wasted context. This gives a skeleton and
    leaves the judgement to the user.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    busiest = ", ".join(f"`{name}`" for name, _ in modules[:8])
    multi = len(entries) > 1
    lines = ["<!-- DRAFT generated by tool-baton. Review and prune before use. -->",
             f"# {root.name}", ""]
    if multi:
        lines += [f"A multi-repository workspace: {len(entries)} independent git "
                  "checkouts side by side. Each subdirectory has its own remote, "
                  "branch and test suite — treat them as separate projects that "
                  "share a parent folder.", "",
                  "## Ground rules", "",
                  "- Never run git commands at the workspace root; use `-C <module>`.",
                  "- A change spanning modules needs one commit and one PR per module.",
                  "- Check a module's branch before editing; they are rarely in sync.",
                  ""]
    if busiest:
        lines += ["## Where work concentrates", "",
                  f"Historically most active: {busiest}.", "",
                  "See `HOT_FILES.md` beside this file for the per-file ranking.",
                  ""]
    lines += ["## Conventions", "",
              "<!-- TODO: fill these in. Good candidates:",
              "     - how to run one module's tests",
              "     - how to bring up a dev instance",
              "     - the release/versioning convention",
              "     - anything the agent got wrong twice -->", "",
              "## Prior context", "",
              f"Chats from the previous agent are archived under `{archive_hint}`.",
              "Grep there before re-deriving a decision; `INDEX.md` lists every",
              "thread by date and title."]
    path = out_dir / filename
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_manifest(payload: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n",
                    encoding="utf-8")
    return path
