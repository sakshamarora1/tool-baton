"""Scrub credentials out of migrated text.

This matters because the Markdown archive is *designed* to be committed to the
user's repository. A year of chat history routinely contains a pasted token, a
`.env` fragment, or a key echoed by a shell command, and moving that from a
private application database into a git-tracked file is a real escalation.

Redaction is on by default and preserves enough shape to stay readable:
`sk-ant-api03-abc…xyz` becomes `sk-ant-…[redacted]`. It is applied only to what
this package writes — never to the user's existing history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ordered most-specific first: a provider-shaped key should be reported as that
# key, not swallowed by the generic assignment rule.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("anthropic-key",
     re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\."
                       r"[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}")),
    ("bearer", re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{20,}")),
    ("private-key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.S)),
    ("basic-auth-url", re.compile(r"\b([a-z][a-z0-9+.\-]*://[^\s:/@]+):[^\s/@]+@")),
    ("assignment", re.compile(
        r"(?i)\b((?:[A-Z0-9_]*)(?:API[_-]?KEY|ACCESS[_-]?KEY|SECRET[_-]?KEY|"
        r"AUTH[_-]?TOKEN|ACCESS[_-]?TOKEN|CLIENT[_-]?SECRET|PASSWORD|PASSWD|"
        r"PRIVATE[_-]?KEY|TOKEN|TOKEN|CREDENTIALS?)"
        r"\s*[:=]\s*)['\"]?([^\s'\"#,;)]{8,})")),
]

PLACEHOLDER = "[redacted]"


@dataclass
class RedactionReport:
    count: int = 0
    kinds: dict[str, int] = field(default_factory=dict)

    def note(self, kind: str) -> None:
        self.count += 1
        self.kinds[kind] = self.kinds.get(kind, 0) + 1

    def merge(self, other: RedactionReport) -> RedactionReport:
        self.count += other.count
        for kind, n in other.kinds.items():
            self.kinds[kind] = self.kinds.get(kind, 0) + n
        return self

    def summary(self) -> str:
        if not self.count:
            return "no secrets detected"
        parts = ", ".join(f"{n}× {kind}"
                          for kind, n in sorted(self.kinds.items(),
                                                key=lambda kv: -kv[1]))
        return f"{self.count} redaction(s): {parts}"


def _keep_prefix(value: str, keep: int = 8) -> str:
    head = value[:keep]
    return f"{head}…{PLACEHOLDER}"


def redact_text(text: str, report: RedactionReport | None = None) -> str:
    """Return `text` with credential-shaped substrings replaced."""
    if not text:
        return text
    report = report if report is not None else RedactionReport()

    for kind, pattern in PATTERNS:
        def replace(match: re.Match, _kind=kind) -> str:
            report.note(_kind)
            if _kind == "private-key":
                return ("-----BEGIN PRIVATE KEY-----"
                        f"{PLACEHOLDER}-----END PRIVATE KEY-----")
            if _kind == "bearer":
                return f"{match.group(1)}{PLACEHOLDER}"
            if _kind == "basic-auth-url":
                return f"{match.group(1)}:{PLACEHOLDER}@"
            if _kind == "assignment":
                return f"{match.group(1)}{PLACEHOLDER}"
            return _keep_prefix(match.group(0))

        text = pattern.sub(replace, text)
    return text


def redact_conversation(conv, report: RedactionReport | None = None):
    """Redact a `Conversation` in place, including tool inputs and results."""
    report = report if report is not None else RedactionReport()
    conv.title = redact_text(conv.title, report)
    if conv.plan:
        conv.plan = redact_text(conv.plan, report)
    for msg in conv.messages:
        msg.text = redact_text(msg.text, report)
        msg.thinking = redact_text(msg.thinking, report)
        for tool in msg.tools:
            if tool.result:
                tool.result = redact_text(tool.result, report)
            tool.input = {
                key: redact_text(value, report) if isinstance(value, str) else value
                for key, value in (tool.input or {}).items()
            }
    return conv
