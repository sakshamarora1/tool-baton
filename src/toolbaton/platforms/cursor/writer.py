"""Write IR conversations into locations Cursor reads.

Cursor stores its own threads as `composerData:<uuid>` and `bubbleId:<uuid>:<uuid>`
rows inside `globalStorage/state.vscdb` — a large SQLite database belonging to a
running application. Inserting rows there is the only way to make a migrated
thread appear in Cursor's history sidebar.

**This package does not do that.** Writing into another application's live
database risks corrupting months of the user's own history for a cosmetic gain,
and no amount of backup ceremony makes that a good default. Instead we write
files Cursor already reads:

  agent-transcripts/<id>/<id>.jsonl   `@`-mentionable, Cursor's own format
  docs/agent-archive/*.md             greppable by any agent
  .cursor/rules/*.mdc                 translated standing instructions

The cost is honest and small: migrated threads are available to the agent, but
not listed in the history sidebar. The README says so plainly.
"""

from __future__ import annotations

from ...ir import Conversation
from ...redact import RedactionReport, redact_conversation
from ..base import WriteOptions, WriteResult
from . import transcripts


def write(conversations: list[Conversation], options: WriteOptions) -> WriteResult:
    out_dir = options.out_dir / "cursor-transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = WriteResult()
    report = RedactionReport()

    for conv in conversations:
        if options.redact:
            redact_conversation(conv, report)
        result.files.append(transcripts.write_transcript(conv, out_dir))

    result.redactions = report.count
    if report.count:
        result.notes.append(f"transcripts: {report.summary()}")
    result.notes.append("migrated threads are @-mentionable in Cursor but do not "
                        "appear in its history sidebar (by design — we never "
                        "write to Cursor's database)")
    return result
