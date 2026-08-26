# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Markdown archive output, plus a portable JSON bundle (`baton export
  --format ir`) that can be replayed on another machine.
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
