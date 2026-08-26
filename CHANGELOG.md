# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **WSL support.** Under WSL an agent installed inside the distribution needs no
  help, but a Windows-hosted Cursor keeps its user data on the Windows volume.
  That location is now discovered through the drive mounts, overridable with
  `BATON_WINDOWS_HOME`, and `baton doctor` reports the distribution and the
  profile it resolved. `util/wsl.py` holds every `/mnt`-shaped assumption so no
  adapter has to learn about it.
- Cursor's remote path spellings are understood: `vscode-remote://wsl+<distro>`,
  `file://wsl.localhost/<distro>`, `file://wsl$/<distro>`, Windows drive letters
  and POSIX paths written with backslashes. Without these a Windows-hosted Cursor
  produced no usable history at all under WSL, because attribution depends on the
  file paths a thread touched and every one of them failed to parse.
- A reader for Cursor's blob chat store, `~/.cursor/chats/<hash>/<agentId>/`
  (`--source chats`). It is the only Cursor source that records tool *results*,
  so threads from it can be written with `--tools blocks` without synthesising
  anything, and it carries reasoning blocks and an exact working directory. Its
  `prompt_history.json` also gives prompt recall without touching the editor's
  database.

### Fixed

- A URI whose authority was not recognised was parsed as a *relative* path, which
  then resolved against the working directory and could land inside the project
  being migrated — attributing another project's prompt history to it. Such URIs
  are now discarded. This was reachable on any platform; WSL just made it common.

## [0.1.0] — unreleased

First public release.

### Added

- Bidirectional migration between **Cursor** and **Claude Code**: chat history,
  standing instructions, prompt history and derived project knowledge.
- A canonical intermediate representation (`toolbaton.ir`) that every adapter
  targets, so adding an agent is one reader plus one writer rather than a
  pairwise integration.
- `baton platforms` — an honest support matrix that distinguishes verified
  adapters from agents that are merely detected.
- `baton probe` — dumps an unsupported agent's storage shape (SQLite schemas,
  key prefixes, JSONL record keys) as the starting point for a new adapter.
- Markdown archive output, plus a portable JSON bundle that is a first-class
  source and destination (`--from bundle` / `--to bundle`). A bundle can be
  carried to another machine, replayed into an agent that did not exist when it
  was made, or used to develop an adapter without installing the source agent.
- Derived knowledge: repository map, hot-file ranking and prompt themes,
  reconstructed from chat metadata rather than from any agent's index.
- Secret redaction, on by default, covering provider keys, bearer tokens, JWTs,
  private-key blocks, `KEY=value` assignments and credentials in URLs.
- `baton retitle` — repairs how migrated threads are named, in three shapes
  (`title`, `compact`, `minimal`), idempotent in each.
- Three tool-call representations (`--tools text|blocks|drop`), because most
  agents record calls without results and an unmatched `tool_use` block makes a
  session unresumable.
- Test suite built on synthesised agent stores, so it runs anywhere without
  private fixtures, plus a guard that fails if real usernames, home paths or
  project names reach a tracked file.

### Notes on scope

- **We never write to another application's database.** Threads migrated into
  Cursor land in its `agent-transcripts` directory: the agent can `@`-mention
  them, but they do not appear in Cursor's history sidebar. Inserting rows into
  a running application's SQLite store risks the user's own history for a
  cosmetic gain, so it is not offered.
- Only Cursor and Claude Code have verified adapters. Windsurf, Copilot, Codex,
  Cline, Continue, Gemini, Qwen and Zed are detected and reported, but have no
  adapter yet — claiming support that has not been exercised against real data
  would be worse than naming the gap.

[Unreleased]: https://github.com/sakshamarora1001/tool-baton/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sakshamarora1001/tool-baton/releases/tag/v0.1.0
