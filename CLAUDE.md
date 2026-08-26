# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`tool-baton` migrates working context — chat history, standing instructions,
prompt history, derived project knowledge — between AI coding agents, in either
direction. Published to PyPI as `tool-baton`; import package `toolbaton`; single
CLI entry point `baton`.

Zero runtime dependencies, on purpose: the tool reads private chat history, so
its supply chain is the standard library and nothing else. Keep it that way.

## Commands

Neither `pytest` nor `ruff` is installed globally here, so use `uv` to run them
in a throwaway environment:

```bash
uv run --with pytest python -m pytest              # full suite (75 tests)
uv run --with pytest python -m pytest tests/test_retitle.py
uv run --with pytest python -m pytest tests/test_retitle.py::test_compact_is_idempotent
uv run --with ruff ruff check .                    # lint (line-length 90, target py39)
uv run --with ruff ruff check --fix .
uv build && uv run --with twine twine check dist/*  # packaging
```

`tests/conftest.py` inserts `src/` on `sys.path`, so pytest needs no
`PYTHONPATH`. Running the module directly does:

```bash
PYTHONPATH=src python3 -m toolbaton.cli platforms
```

The alternative for a persistent env is `pip install -e ".[dev]"`, after which
`pytest` and the `baton` entry point work directly.

Python floor is **3.9** — CI runs 3.9 through 3.13 on Linux, macOS and Windows.
Every module uses `from __future__ import annotations`, which is what lets `X | Y`
annotations work on 3.9; keep that import when adding a module.

## Architecture

### The IR is the seam

`src/toolbaton/ir.py` holds the canonical model: `Conversation`, `Message`,
`ToolCall`. **Adapters read into the IR and write out of it, and never talk to
each other.** Adding an agent is therefore one reader plus one writer — never a
pairwise integration with every other agent. When adding a feature, ask whether
it belongs in the IR (true of every agent) or in one adapter (true of one).

`merge_sources()` exists because a single agent can store the same thread more
than once at different fidelity. Cursor is the case in point, with three stores
keyed on the same id: its SQLite store has every thread but a noisy message
stream, its JSONL transcripts are clean but cover only recent ones and drop tool
results, and its blob store (`~/.cursor/chats`) keeps results and reasoning but is
newer still. `read()` layers them cleanest-last and keeps SQLite's metadata.

### Platforms

`platforms/base.py` defines the contract. Two things about it are deliberate:

- **Capabilities are declared, not inferred** (`READ`, `WRITE`, `RULES`,
  `PROMPTS`, `RETITLE`). `require()` raises `Unsupported` for anything absent.
- **`verified` records whether an adapter has been exercised against real data.**
  `baton platforms` prints it. Claiming support that has not been demonstrated is
  worse than naming the gap — do not set `verified = True` without having run the
  adapter against a real installation.

`platforms/__init__.py` is a lazy registry: adapters are imported on first use,
and `all_platforms()` swallows import errors so one broken adapter cannot stop
the CLI from starting. `_DETECT_ONLY` entries are agents recognised but not yet
supported; they are listed on purpose so an unsupported user is told so rather
than left guessing.

`DetectOnly` subclasses only need `name`, `label` and `paths`.

### Paths are not all POSIX

`util/wsl.py` holds every `/mnt`-shaped assumption; nothing else may grow one.
Under WSL an agent installed inside the distribution stores where any Linux
install would, but a Windows-hosted Cursor keeps its data on the Windows volume,
which is why `electron_app_support` takes a `marker` and picks the candidate that
actually contains it rather than choosing by platform.

`uri_to_path` must return `None` for anything it does not recognise. It once
stripped a fixed `file://` prefix, so a URI with a non-empty authority became a
*relative* path, resolved against the cwd, and landed inside the project being
migrated — silently attributing another project's history to it. Cursor spells one
WSL file four ways (`vscode-remote://wsl+<distro>`, `file://wsl.localhost/...`,
`file://wsl$/...`, and POSIX paths with backslashes), and a path in another
distribution is deliberately dropped because it is not reachable.

### Five format landmines

These are undocumented behaviours of the target agents. Each was found by
experiment, and each fails silently or at runtime rather than at write time.

1. **The two project-slug conventions differ by one character.**
   `util/paths.py::slugify_path(keep_leading=)` — Claude Code keeps the leading
   separator (`-Users-you-code-repo`), Cursor drops it (`Users-you-code-repo`).
   Get it wrong and written data lands somewhere the agent never looks.

2. **A session's filename must equal the `sessionId` inside it**, or Claude Code
   will not list it in `--resume`.

3. **An assistant `tool_use` block with no matching `tool_result` makes a session
   unresumable** — the API rejects the conversation. Most agents record calls but
   not results, which is the entire reason `--tools` has three modes
   (`text` flattens to prose, `blocks` synthesises the result, `drop` omits).
   Default is `text` because it is always safe.

4. **Claude Code's resume picker renders the first user message, flattened onto
   one line — not the `summary` record.** So the synthetic preamble in
   `platforms/claude_code/writer.py::preamble` must lead with the title and
   nothing else: no HTML comment, no heading marker. `retitle.py` exists to
   repair sessions written before this was understood, and keeps `summary` in
   step with the first turn.

5. **`latestRootBlobId` is not the conversation.** In `~/.cursor/chats`, every
   turn writes a new node listing the whole sequence so far, and the id in `meta`
   names the agent's *live context*. After `/summarize` they diverge completely:
   on a real 184-message thread it pointed at a three-message context whose
   history had been replaced by a précis, while the full thread sat in a node
   nothing referenced. `chats.py::conversation_refs` therefore takes the node
   covering the most messages and uses the recorded id only to break a tie.

### Two hard invariants

- **Never write to another application's database.** Cursor keeps history in a
  large SQLite file belonging to a running app; inserting rows would risk the
  user's own history for a cosmetic gain. `platforms/cursor/writer.py` therefore
  writes only files Cursor already reads, and its docstring explains the trade.
  Do not add a database-write path.
- **Read databases only through `util/db.py::DBSnapshot`**, which copies the file
  plus any `-wal`/`-shm` sidecars and opens the copy `mode=ro`. This is what makes
  reading a live application's store safe.

### Choices that look like preferences but are load-bearing

- **Redaction defaults on** (`redact.py`). The Markdown archive is *designed* to
  be committed to the user's repo, so an unredacted secret moves from a private
  database into git history. On real data this fires regularly.
- **The default output directory is a deterministic path under the system temp
  dir**, not `mkdtemp` (`util/paths.py::default_output_dir`). `migrate` and
  `install` are separate processes, so a random directory would leave `install`
  unable to find the build.
- **Ids are `uuid5` from a fixed namespace** (`writer.py::NS`), so re-running a
  migration overwrites rather than duplicating.
- **`retitle` must stay idempotent in all three modes.** `compact` previously
  degraded on a second pass: it read metadata from bullets its own first pass had
  collapsed, found none, and dropped the line. It now falls back to reusing its
  own `_COMPACT_LINE`. `tests/test_retitle.py` pins this.

## Tests

Fixtures are **synthesised, not committed** — real ones would be private chat
history. `tests/fixtures/build.py` constructs a Cursor `state.vscdb` with the real
schema, Cursor JSONL transcripts, and Claude Code sessions both native and
previously-imported. When an agent's on-disk format changes, encode it there.

`tests/test_no_private_data.py` walks every git-tracked file and fails on the
real `$USER`, on concrete home-directory paths, and on a project-name denylist.
**So use placeholders like `/Users/you/code/myrepo` in code, docstrings and docs.**
Its `PUBLIC_IDENTITY` list exempts the package's declared author email and
repository URL, which a published package must carry.

`*-out/` is gitignored: a migration output directory holds real conversations.

## Adding an agent adapter

1. `baton probe <path>` dumps the agent's SQLite schemas, key prefixes and JSONL
   record shapes.
2. Add `platforms/<name>/` with a `Platform` subclass exposing `PLATFORM`, and
   register it in `platforms/__init__.py::_IMPLEMENTED`.
3. Reach `ir.Conversation` — that is the only contract. Never reference another
   adapter.
4. Add a fixture builder and a round-trip test.
5. Leave `verified = False` until it has run against a real installation.
