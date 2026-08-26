# tool-baton

Pass your context between AI coding agents. Chat history, standing instructions,
prompt history and derived project knowledge — moved from one agent to another,
in either direction.

```bash
pipx install tool-baton          # or: uv tool install tool-baton
baton doctor                     # what's on this machine
baton migrate --from cursor --to claude-code
baton install --to claude-code --yes
```

Nothing is written outside the build directory until you pass `--yes`. Source
agents' data is only ever **read**, and only from snapshot copies.

---

## Why this exists

Switching coding agents means abandoning everything the old one knew. Rules can
be retyped in an afternoon; a year of conversations cannot. Most of what makes an
agent feel like it understands your project is not in a config file — it's in the
threads where you explained the same architecture six times.

`tool-baton` moves that. It reads the source agent's own storage, normalises it
into a small canonical model, and writes it into the destination's format.

---

## What actually transfers

The honest version, because it varies a lot by direction.

| | Cursor → Claude Code | Claude Code → Cursor |
|---|---|---|
| Chat history | resumable sessions + Markdown archive | `@`-mentionable transcripts + Markdown archive |
| Tool calls | yes | yes, **with results** |
| Standing instructions | `.cursor/rules/*.mdc` → `.claude/rules/`, skills | `CLAUDE.md`, `.claude/rules/` → `.cursor/rules/*.mdc` |
| Ignore rules | `.cursorignore` → `permissions.deny` | `permissions.deny` → `.cursorignore` |
| Prompt history | → `~/.claude/history.jsonl` | not writable |
| Repo map, hot files | yes | yes |
| Codebase index | nothing to move — see below | nothing to move |

### Two asymmetries worth knowing before you start

**Tool results.** Cursor records that its agent called `read_file`, but usually
not what came back. Claude Code records both. So Claude Code → Cursor carries
real tool output, and Cursor → Claude Code carries calls with mostly empty
results. See [tool call handling](#tool-call-handling).

**Where threads land.** Claude Code reads sessions from a directory, so migrated
threads appear in `claude --resume` alongside native ones. Cursor keeps history
inside a large SQLite database belonging to a running application. **We do not
write to it.** Migrated threads land in Cursor's `agent-transcripts` directory
instead: the agent can `@`-mention them, but they do not appear in Cursor's
history sidebar. That is a deliberate trade — no cosmetic gain is worth a chance
of corrupting months of someone's own history.

### About the codebase index

There is nothing to migrate, and that is good news. Some agents maintain a vector
index of your repository; an index is a cache of the tree, and the destination
will build its own or read the tree directly.

What *is* worth salvaging is the signal that index accumulated indirectly:
**which files you actually opened, attached and edited, how often, and how
recently.** `baton migrate` reconstructs that from chat metadata and writes a
ranked hot-file list plus a repository map. That is the useful residue, in a form
any agent can read.

---

## Install

Zero runtime dependencies — the standard library only. A tool that reads private
chat history should have the smallest supply chain possible.

```bash
pipx install tool-baton
uv tool install tool-baton
uvx tool-baton doctor            # run without installing
```

Requires Python 3.9+. Works on macOS, Linux and Windows; path layouts for all
three are handled, and CI exercises each.

---

## Commands

```
baton platforms                                    # support matrix, both directions
baton doctor                                       # detect agents and data here
baton inventory --from cursor                      # list what the source holds
baton migrate   --from cursor --to claude-code     # build into --out
baton install   --to claude-code                   # apply (dry run without --yes)
baton retitle   --to claude-code                   # rename already-written threads
baton export    --from cursor --format ir          # portable JSON bundle
baton probe     <path>                             # inspect an unsupported agent
baton clean                                        # remove the build directory
```

### `doctor`

Start here. Reports every agent found, where its data lives, how much there is,
and where output will go. Writes nothing.

### `inventory`

Lists conversations the source holds for `--project`, newest first.

```bash
baton inventory --from cursor --project ~/code/myrepo
baton inventory --from cursor --since 2026-01 --json convs.json
```

Attribution is per-conversation, not per-workspace: agents that keep every
project's chats in one store need it. A thread belongs to `--project` if any file
it read, attached or edited lives under that root.

### `migrate`

Builds everything into `--out` without touching a live location:

```
out/
├── sessions/            or cursor-transcripts/   the destination's own format
├── archive/
│   ├── INDEX.md                                  every thread, by date
│   └── 2026-06-01-topic-6878d8e5.md
├── knowledge/
│   ├── REPO_MAP.md      HOT_FILES.md    PROMPT_THEMES.md
│   └── CLAUDE.md.draft                           a draft, never auto-installed
├── config/                                       translated rules and ignores
├── history/history.jsonl                         prompt recall
└── bundle.json                                   portable IR
```

Use both main outputs. **Sessions** let you reopen an old thread and keep going.
The **Markdown archive** is what carries knowledge day to day — agents grep and
read files, so a readable archive in the repo beats a resumable session you have
to hunt for.

```bash
baton migrate --from cursor --to claude-code --mode compact
baton migrate --from claude-code --to cursor
baton migrate --from cursor --to markdown --limit 40 --min-messages 6
```

Session ids and message uuids are derived deterministically, so re-running is
idempotent rather than duplicating.

### `install`

Copies the build into live locations. **Dry run by default.**

| From `--out` | To (Claude Code) |
|---|---|
| `sessions/` | `~/.claude/projects/<slug>/` |
| `archive/` | `<project>/docs/agent-archive/` |
| `knowledge/` | `<project>/.claude/context/` |
| `config/.claude/rules/` | `<project>/.claude/rules/` |
| `history/history.jsonl` | appended to `~/.claude/history.jsonl` (backed up first) |

Two things are never installed automatically, because both are files you already
curate: the **instruction-file draft**, and **`settings.json`** (the ignore-rule
conversion is left as a fragment to merge by hand).

### `retitle`

Fixes how migrated threads are named, in place.

A session's `summary` record carries a title, but Claude Code's resume picker
renders the **first user message**, flattened onto one line. A thread whose first
turn opens with a provenance banner therefore displays as the banner. `retitle`
rewrites that turn and keeps `summary` in step.

| `--mode` | Picker shows |
|---|---|
| `title` *(default)* | `[baton] Understanding the scan API - Source id: …` |
| `compact` | `[baton] Understanding the scan API source:1922328d · 2025-10-20 → 2025-10-22` |
| `minimal` | `[baton] Understanding the scan API` |

```bash
baton retitle --to claude-code --list                    # what shows now
baton retitle --to claude-code --mode compact --yes
baton retitle --to claude-code --rename <id>="Better title" --yes
```

Idempotent in every mode; each rewritten file gets a `.jsonl.bak-<epoch>` beside
it; only threads this tool wrote are touched unless you pass `--all`.

### `probe`

For an agent with no adapter yet. Point it at a directory and it reports the
SQLite schemas, key prefixes and JSONL shapes it finds — the input needed to
write a reader.

```bash
baton probe ~/.some-agent
```

---

## Tool call handling

The one place fidelity is genuinely lost. Most agents record tool *calls* but not
tool *results*, and the Anthropic API rejects an assistant `tool_use` block with
no matching `tool_result` — so copying blocks across verbatim yields a session
that fails the moment you resume it.

| `--tools` | Behaviour | Resumable |
|---|---|---|
| `text` *(default)* | each call rendered as text in the assistant turn | yes |
| `blocks` | real `tool_use` blocks plus a synthesised `tool_result` | yes |
| `drop` | calls omitted entirely | yes |

Every migrated thread opens with a note saying it came from another agent and
that tool output is historical — so a resumed thread does not act on stale file
contents.

---

## Secrets

Chat history routinely contains a pasted token, a `.env` fragment, or a key
echoed by a shell command. The Markdown archive is *designed* to be committed to
your repository, which turns that from a private database record into git
history.

So **redaction is on by default**. Provider-shaped keys, bearer tokens, JWTs,
private-key blocks, `KEY=value` assignments and credentials in URLs are replaced
with `[redacted]`, and the count is reported. Ordinary prose about API keys is
left alone. `--no-redact` opts out.

Redaction applies only to what this tool writes. Your existing history is never
modified.

---

## Safety

- **Source data is never written.** Databases are read from a `shutil.copy2`
  snapshot (including `-wal`/`-shm` sidecars) opened `mode=ro`.
- **We never write to another application's database.** See the asymmetry note.
- **Build and install are separate.** `migrate` only writes under `--out`.
- **`~/.claude/history.jsonl` is backed up** before appending, and duplicate
  records are skipped, so installing twice is safe.
- **Instruction files and `settings.json` are never overwritten.**
- **Nothing leaves the machine.** No network calls, no telemetry.

Default output goes to a stable directory under the system temp dir, keyed on the
project — not the working directory. It is deterministic so `install` can find
what `migrate` produced. Pass `--out` if the build needs to outlive a reboot.

---

## Supported agents

`baton platforms` prints this live:

| Agent | Read | Write | Rules | Verified |
|---|---|---|---|---|
| Cursor | yes | file-based | yes | yes |
| Claude Code | yes | yes | yes | yes |
| Markdown | — | yes | — | yes |
| Windsurf, Copilot, Codex, Cline, Continue, Gemini, Qwen, Zed | detect only | — | — | no |

Detect-only agents are listed deliberately rather than omitted: if your agent is
unsupported you should be told so, not left guessing.

**Adding an agent is one reader plus one writer.** Both talk only to the
canonical model in `toolbaton/ir.py` — never to another agent's format — so an
adapter never grows pairwise integrations. `baton probe` plus the `create-adapter`
skill walk you through it. Contributions welcome; the fixture builder in
`tests/fixtures/build.py` shows how to test an adapter without committing private
data.

---

## Development

```bash
git clone https://github.com/sakshamarora1001/tool-baton
cd tool-baton
uv run --with pytest python -m pytest      # or: pip install -e ".[dev]" && pytest
uv run --with ruff ruff check .
```

The suite builds throwaway agent stores rather than shipping fixtures — real ones
would be private chat history. `tests/test_no_private_data.py` walks every tracked
file and fails on real usernames, home paths or project names, so the repository
cannot pick them up later.

### Layout

```
src/toolbaton/
├── cli.py            command line surface
├── ir.py             the canonical model every adapter targets
├── redact.py         secret scrubbing
├── knowledge.py      repo map, hot files, prompt themes
├── rules.py          bidirectional instruction translation
├── util/             shared paths and safe SQLite reads
└── platforms/
    ├── base.py       the adapter contract
    ├── cursor/       reader, transcripts, writer
    ├── claude_code/  reader, writer, retitle, history
    └── markdown/     writer
```

Each adapter's module docstring documents the exact storage keys it depends on.

---

## Licence

MIT.
