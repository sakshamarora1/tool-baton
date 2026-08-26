"""Command-line interface.

Nothing writes outside `--out` unless you run `install --yes` or `retitle --yes`.
Source agents' state is only ever read, and only from snapshot copies.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, ir, knowledge, platforms
from .platforms.base import (
    PROMPTS,
    READ,
    RETITLE,
    RULES,
    WRITE,
    ReadOptions,
    Unsupported,
    WriteOptions,
    WriteResult,
)
from .util import wsl
from .util.paths import default_output_dir

DEFAULT_PREFIX = "[baton] "


def say(message: str = "") -> None:
    print(message, flush=True)


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #


def _read_options(args) -> ReadOptions:
    return ReadOptions(project=args.project, source=args.source, since=args.since,
                       limit=args.limit, min_messages=args.min_messages)


def _write_options(args) -> WriteOptions:
    return WriteOptions(project=args.project, out_dir=args.out, tools=args.tools,
                        prefix=args.prefix, mode=args.mode,
                        include_thinking=not args.no_thinking,
                        redact=not args.no_redact,
                        target_version=args.target_version or None)


def _collect(args) -> list:
    """Read conversations from the source platform and apply filters."""
    source = platforms.get(args.source_platform)
    try:
        conversations = source.read(_read_options(args))
    except Unsupported as exc:
        die(str(exc))
    except FileNotFoundError as exc:
        die(str(exc))

    if args.since:
        conversations = [c for c in conversations
                         if (c.updated_at or c.created_at or "") >= args.since]
    conversations = [c for c in conversations
                     if len(c.messages) >= args.min_messages]
    conversations.sort(key=lambda c: c.updated_at or c.created_at or "",
                       reverse=True)
    if args.limit:
        conversations = conversations[:args.limit]
    return conversations


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_platforms(args) -> int:
    say(f"tool-baton {__version__}")
    say("")
    say(f"{'platform':14s} {'read':6s} {'write':6s} {'rules':6s} "
        f"{'prompts':8s} {'here':6s} notes")
    def mark(platform, cap: str) -> str:
        return "yes" if platform.supports(cap) else "-"

    for platform in platforms.all_platforms():
        here = "yes" if platform.detect() else "-"
        note = platform.caveat
        if not platform.capabilities:
            note = note or "no adapter yet"
        elif not platform.verified:
            note = (note + "; " if note else "") + "unverified"
        say(f"{platform.name:14s} {mark(platform, READ):6s} "
            f"{mark(platform, WRITE):6s} {mark(platform, RULES):6s} "
            f"{mark(platform, PROMPTS):8s} {here:6s} {note}")
    say("")
    say("'here' means the agent was found on this machine, not that it holds data.")
    say("For an agent with no adapter: `baton probe <path>` dumps its storage "
        "shape, and the create-adapter skill turns that into a working adapter.")
    return 0


def cmd_doctor(args) -> int:
    say(f"tool-baton {__version__}")
    say(f"project      {args.project}")
    say(f"default out  {default_output_dir(args.project)}")
    if wsl.is_wsl():
        home = wsl.windows_home()
        say(f"environment  WSL ({wsl.distro() or 'distro unknown'}), "
            f"windows home {home or 'not found'}")
    say("")
    found = 0
    for platform in platforms.all_platforms():
        if not platform.detect():
            continue
        found += 1
        tag = "" if platform.capabilities else "   (detect only, no adapter)"
        say(f"{platform.label}{tag}")
        for line in platform.describe(args.project):
            say(line)
        say("")
    if not found:
        say("no supported agent found. Check CURSOR_APP_SUPPORT / CLAUDE_CONFIG_DIR "
            "if your install is in a non-standard location.")
        if wsl.is_wsl():
            say("under WSL, a Windows-side editor is found through the drive "
                "mounts; set BATON_WINDOWS_HOME if your profile is elsewhere.")
        return 1
    say(f"{found} agent(s) detected. `baton platforms` shows what each can do.")
    return 0


def cmd_inventory(args) -> int:
    conversations = _collect(args)
    if not conversations:
        say("no conversations matched")
        return 1
    total = sum(len(c.messages) for c in conversations)
    tools = sum(c.tool_calls for c in conversations)
    say(f"{len(conversations)} conversation(s), {total} message(s), "
        f"{tools} tool call(s) from {args.source_platform}")
    say("")
    say(f"{'last active':20s} {'msgs':>5s}  {'source':<22s} title")
    for conv in conversations:
        say(f"{(conv.updated_at or '')[:19]:20s} {len(conv.messages):5d}  "
            f"{conv.source:<22s} {conv.title[:70]}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(ir.to_bundle(conversations, args.project,
                                    args.source_platform), indent=2,
                       default=str) + "\n", encoding="utf-8")
        say(f"\nwrote {args.json}")
    return 0


def cmd_migrate(args) -> int:
    source = platforms.get(args.source_platform)
    target = platforms.get(args.target_platform)
    if not target.supports(WRITE):
        die(f"{target.label} cannot be a destination. "
            f"Run `baton platforms` to see what can.")

    conversations = _collect(args)
    if not conversations:
        say("no conversations matched — nothing written")
        return 1

    options = _write_options(args)
    result = WriteResult()

    # The target's own format, plus a Markdown archive always: it is the one
    # output that stays readable after any agent goes away.
    result.extend(target.write(conversations, options))
    if target.name != "markdown":
        result.extend(platforms.get("markdown").write(conversations, options))

    # Standing instructions, in the target's dialect.
    if target.supports(RULES) and hasattr(target, "write_rules"):
        result.extend(target.write_rules(args.project, options))

    # Prompt history, where the target has somewhere to put it.
    if target.supports(PROMPTS) and hasattr(source, "read_prompts"):
        prompts = source.read_prompts(args.project)
        if hasattr(target, "write_prompts"):
            result.extend(target.write_prompts(prompts, options))

    # Derived knowledge, which no agent exports.
    if not args.no_knowledge:
        result.extend(_write_knowledge(conversations, args, target))

    ir.write_bundle(conversations, args.project, args.source_platform, args.out)

    say(f"{len(conversations)} conversation(s): {source.label} → {target.label}")
    say(f"  out       {args.out}")
    say(f"  files     {len(result.files)}")
    if result.redactions:
        say(f"  redacted  {result.redactions} secret(s)")
    for note in result.notes:
        say(f"  note      {note}")
    say("")
    say(f"Review it, then: baton install --to {target.name} "
        f"--project {args.project} --out {args.out}")
    return 0


def _write_knowledge(conversations, args, target) -> WriteResult:
    out_dir = args.out / "knowledge"
    entries = knowledge.repo_map(args.project)
    modules = knowledge.hot_modules(conversations, args.project)
    files = knowledge.hot_files(conversations, args.project)

    written = [knowledge.write_repo_map(entries, out_dir),
               knowledge.write_hot_files(files, modules, out_dir,
                                         len(conversations))]

    seed_name = {"claude-code": "CLAUDE.md.draft",
                 "cursor": "cursor-rules.draft.md"}.get(target.name,
                                                        "INSTRUCTIONS.draft.md")
    written.append(knowledge.write_seed_instructions(
        args.project, entries, modules, out_dir, filename=seed_name))

    source = platforms.get(args.source_platform)
    prompts = source.read_prompts(args.project) if hasattr(source, "read_prompts") else []
    if prompts:
        written.append(knowledge.write_prompt_themes(prompts, out_dir))

    knowledge.write_manifest({
        "tool": "tool-baton", "version": __version__,
        "project": str(args.project),
        "from": args.source_platform, "to": args.target_platform,
        "conversations": len(conversations), "repos": len(entries),
        "prompts": len(prompts), "hot_files": len(files),
    }, args.out)
    return WriteResult(files=written,
                       notes=[f"seed instructions are a draft: {out_dir / seed_name}"])


def cmd_install(args) -> int:
    target = platforms.get(args.target_platform)
    if not args.out.is_dir():
        die(f"{args.out} does not exist — run `baton migrate` first")

    apply = args.yes
    result = target.install(args.out, args.project, apply)

    say(f"install → {target.label}"
        + ("" if apply else "   [DRY RUN — pass --yes to apply]"))
    say("")
    for path in result.files[:200]:
        say(f"  {path}")
    if len(result.files) > 200:
        say(f"  ... and {len(result.files) - 200} more")
    for note in result.notes:
        say(f"  note: {note}")

    seed = next(args.out.glob("knowledge/*.draft*"), None)
    if seed:
        say("")
        say(f"Instruction file NOT installed — it is a draft. Review {seed} first.")

    fragment = args.out / "config" / "settings.deny-fragment.json"
    if fragment.is_file():
        say(f"Merge {fragment} into .claude/settings.json by hand.")

    say("")
    if apply:
        say(f"{len(result.files)} file(s) copied.")
    else:
        say(f"{len(result.files)} file(s) would be copied. Re-run with --yes.")
    return 0


def cmd_retitle(args) -> int:
    target = platforms.get(args.target_platform)
    if not target.supports(RETITLE):
        die(f"{target.label} does not support retitling.")
    from .platforms.claude_code import retitle as rt

    directory = args.dir or target.session_dir(args.project)
    if not directory.is_dir():
        die(f"no sessions at {directory}")

    if args.list:
        rows = rt.list_titles(directory, prefix=args.prefix)
        say(f"{len(rows)} session(s) in {directory}")
        say("")
        say(f"{'session':38s} {'kind':9s} shown in the picker")
        for row in rows:
            say(f"{row.session_id:38s} {row.note:9s} {row.old_title[:80]}")
        return 0

    renames: dict[str, str] = {}
    for item in args.rename:
        session_id, sep, title = item.partition("=")
        if not sep or not title.strip():
            die(f"--rename expects <sessionId>=<new title>, got {item!r}")
        renames[session_id.strip()] = title.strip()

    dry = not args.yes
    if not dry:
        say("note: a session currently open in the target agent may overwrite "
            "these edits on exit. Quit it first.")
        say("")

    results = rt.retitle_dir(directory, prefix=args.prefix, mode=args.mode,
                             renames=renames, apply=not dry,
                             only_imported=not args.all)
    changed = [r for r in results if r.changed]

    say(f"{directory}   mode={args.mode}  prefix={args.prefix!r}"
        + ("   [DRY RUN — pass --yes to apply]" if dry else ""))
    say("")
    for result in changed:
        say(f"  {result.session_id}")
        say(f"    before  {result.old_title[:96]}")
        say(f"    after   {result.new_title[:96]}")
    if not changed:
        say("  nothing to change")

    reasons: dict[str, int] = {}
    for result in results:
        if not result.changed:
            key = result.note or "unchanged"
            reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        say("")
        say("skipped: " + ", ".join(f"{n}× {note}"
                                   for note, n in sorted(reasons.items())))
    say("")
    if dry:
        say(f"{len(changed)} session(s) would be rewritten. Re-run with --yes.")
        if args.mode == "title" and changed:
            say("For a title with no trailing metadata: --mode compact or minimal.")
    else:
        say(f"{len(changed)} rewritten; each has a .bak-<epoch> alongside it.")
        say(f"Clean up with:  rm {directory}/*.jsonl.bak-*")
    return 0


def cmd_export(args) -> int:
    conversations = _collect(args)
    if not conversations:
        say("no conversations matched")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    if args.format == "ir":
        path = ir.write_bundle(conversations, args.project,
                               args.source_platform, args.out)
        say(f"wrote {path}  ({len(conversations)} conversation(s))")
        say(f"Replay it anywhere with:  baton migrate --from bundle "
            f"--source {path} --to <platform>")
    else:
        result = platforms.get("markdown").write(conversations,
                                                 _write_options(args))
        say(f"wrote {len(result.files)} file(s) under {args.out / 'archive'}")
        if result.redactions:
            say(f"redacted {result.redactions} secret(s)")
    return 0


def cmd_probe(args) -> int:
    """Dump the shape of an unknown agent's storage.

    The on-ramp for an agent with no adapter: point this at a directory or file
    and it reports what it finds, which is the input the create-adapter skill
    needs to write a reader.
    """
    target = Path(args.path).expanduser()
    if not target.exists():
        die(f"no such path: {target}")

    say(f"probing {target}")
    say("")
    databases, jsonl, json_files = [], [], []
    if target.is_file():
        candidates = [target]
    else:
        candidates = [p for p in target.rglob("*")
                      if p.is_file() and "node_modules" not in p.parts]

    for path in candidates[:20000]:
        suffix = path.suffix.lower()
        if suffix in (".vscdb", ".db", ".sqlite", ".sqlite3"):
            databases.append(path)
        elif suffix == ".jsonl":
            jsonl.append(path)
        elif suffix == ".json":
            json_files.append(path)

    say(f"{len(databases)} sqlite, {len(jsonl)} jsonl, {len(json_files)} json")
    say("")

    import sqlite3

    from .util.db import DBSnapshot

    for path in databases[:5]:
        say(f"--- sqlite {path}  ({path.stat().st_size / 1e6:.1f} MB)")
        try:
            with DBSnapshot(path) as snap:
                conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")]
                for table in tables:
                    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    say(f"    table {table}  ({count} rows)")
                    if count and table.lower().endswith(("kv", "itemtable")):
                        rows = conn.execute(
                            f'SELECT key FROM "{table}" LIMIT 2000').fetchall()
                        prefixes: dict[str, int] = {}
                        for (key,) in rows:
                            key = key if isinstance(key, str) else str(key)
                            head = key.split(":")[0].split(".")[0]
                            prefixes[head] = prefixes.get(head, 0) + 1
                        top = sorted(prefixes.items(), key=lambda kv: -kv[1])[:12]
                        for head, n in top:
                            say(f"      key prefix {head!r}  ×{n}")
                conn.close()
        except Exception as exc:
            say(f"    unreadable: {exc}")
        say("")

    for path in jsonl[:5]:
        say(f"--- jsonl {path}")
        with path.open(encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= 2:
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                say(f"    keys: {sorted(record)[:14]}")
        say("")

    say("Next: the create-adapter skill turns this into a reader. The target is "
        "toolbaton.ir.Conversation — that is the only contract an adapter needs.")
    return 0


def cmd_clean(args) -> int:
    import shutil

    target = args.out
    if not target.is_dir():
        say(f"nothing at {target}")
        return 0
    count = sum(1 for p in target.rglob("*") if p.is_file())
    if not args.yes:
        say(f"would delete {count} file(s) under {target}")
        say("Re-run with --yes to delete.")
        return 0
    shutil.rmtree(target)
    say(f"deleted {count} file(s) from {target}")
    return 0


# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="baton",
        description="Pass your context between AI coding agents: chat history, "
                    "standing instructions and derived project knowledge.")
    parser.add_argument("--version", action="version", version=__version__)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", type=Path, default=Path.cwd(),
                        help="repository the agent was used on (default: cwd)")
    common.add_argument("--out", type=Path, default=None,
                        help="build directory (default: a stable path under the "
                             "system temp dir, printed by `baton doctor`)")
    common.add_argument("--from", dest="source_platform", default="cursor",
                        help="source platform (see `baton platforms`)")
    common.add_argument("--to", dest="target_platform", default="claude-code",
                        help="destination platform (see `baton platforms`)")
    common.add_argument("--source", default="auto",
                        help="adapter-specific sub-source, e.g. "
                             "auto|sqlite|transcripts|chats")
    common.add_argument("--since", default="",
                        help="drop conversations older than this ISO date, e.g. 2026-01")
    common.add_argument("--limit", type=int, default=0,
                        help="keep only the N most recently active conversations")
    common.add_argument("--min-messages", type=int, default=2,
                        help="drop conversations with fewer messages (default: 2)")
    common.add_argument("--tools", choices=("text", "blocks", "drop"),
                        default="text",
                        help="how to represent tool calls (default: text)")
    common.add_argument("--prefix", default=DEFAULT_PREFIX,
                        help=f"title prefix (default: {DEFAULT_PREFIX!r})")
    common.add_argument("--mode", choices=("title", "compact", "minimal"),
                        default="title",
                        help="preamble shape: title keeps the metadata block, "
                             "compact puts it on one line, minimal is title only")
    common.add_argument("--no-thinking", action="store_true",
                        help="omit the model's reasoning text")
    common.add_argument("--no-redact", action="store_true",
                        help="do not scrub credential-shaped strings (not advised: "
                             "the archive is meant to be committed)")
    common.add_argument("--no-knowledge", action="store_true",
                        help="skip the repo map and hot-file ranking")
    common.add_argument("--target-version", default="",
                        help="version string to stamp into emitted records "
                             "(default: ask the destination CLI)")
    common.add_argument("--json", default="",
                        help="inventory: also write an IR bundle here")
    common.add_argument("--yes", action="store_true",
                        help="actually write (install, retitle, clean)")

    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn, help_text in (
        ("platforms", cmd_platforms, "what is supported, and in which direction"),
        ("doctor", cmd_doctor, "detect agents and data on this machine"),
        ("inventory", cmd_inventory, "list conversations the source holds"),
        ("migrate", cmd_migrate, "build a migration into --out"),
        ("install", cmd_install, "copy built output into the destination"),
        ("clean", cmd_clean, "remove the build directory"),
    ):
        p = sub.add_parser(name, parents=[common], help=help_text)
        p.set_defaults(func=fn)

    ex = sub.add_parser("export", parents=[common],
                        help="write an IR bundle or a Markdown archive")
    ex.set_defaults(func=cmd_export)
    ex.add_argument("--format", choices=("ir", "markdown"), default="ir",
                    help="ir: portable JSON bundle; markdown: readable archive")

    rt = sub.add_parser("retitle", parents=[common],
                        help="rename threads already written to a destination")
    rt.set_defaults(func=cmd_retitle)
    rt.add_argument("--dir", type=Path, default=None,
                    help="session directory (default: the one for --project)")
    rt.add_argument("--rename", action="append", default=[], metavar="ID=TITLE",
                    help="set one thread's title explicitly; repeatable")
    rt.add_argument("--list", action="store_true",
                    help="show current titles; writes nothing")
    rt.add_argument("--all", action="store_true",
                    help="also touch threads this tool did not write")

    pr = sub.add_parser("probe", parents=[common],
                        help="dump an unknown agent's storage shape")
    pr.set_defaults(func=cmd_probe)
    pr.add_argument("path", help="file or directory to inspect")

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.project = args.project.expanduser().resolve()
    if not args.project.is_dir():
        die(f"not a directory: {args.project}")
    args.out = (args.out.expanduser().resolve() if args.out
                else default_output_dir(args.project))
    try:
        return args.func(args) or 0
    except Unsupported as exc:
        die(str(exc))
    except KeyError as exc:
        die(str(exc).strip("'\""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
