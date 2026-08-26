"""Redaction.

The Markdown archive is meant to be committed, so a secret that survives the
migration moves from a private database into git history.
"""

from __future__ import annotations

import pytest

from toolbaton.ir import Conversation, Message, ToolCall
from toolbaton.redact import RedactionReport, redact_conversation, redact_text


@pytest.mark.parametrize("secret", [
    "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "xoxb-1234567890-abcdefghijklmno",
])
def test_provider_keys_are_removed(secret):
    out = redact_text(f"the key is {secret} ok")
    assert secret not in out
    assert "[redacted]" in out


def test_assignments_and_urls_are_removed():
    assert "hunter2secret" not in redact_text("DB_PASSWORD=hunter2secret")
    assert "s3cr3tvalue" not in redact_text("postgres://u:s3cr3tvalue@host/db")


def test_bearer_keeps_the_scheme_but_drops_the_token():
    out = redact_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123")
    assert "abcdefghijklmnopqrstuvwxyz0123" not in out
    assert "Bearer" in out


def test_private_key_block_collapses():
    body = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n"
            "-----END RSA PRIVATE KEY-----")
    out = redact_text(body)
    assert "MIIEowIBAAKCAQEA" not in out


def test_ordinary_prose_is_untouched():
    prose = "We should rotate api keys and tokens more often than once a year."
    assert redact_text(prose) == prose


def test_report_counts_and_summarises():
    report = RedactionReport()
    redact_text("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", report)
    assert report.count == 1
    assert "github-token" in report.summary()


def test_conversation_is_redacted_including_tool_payloads():
    conv = Conversation(id="x", title="t", messages=[
        Message(id="1", role="assistant", text="see AKIAIOSFODNN7EXAMPLE",
                thinking="also AKIAIOSFODNN7EXAMPLE",
                tools=[ToolCall(
                    name="Bash",
                    input={"command": "export T=ghp_"
                                      "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"},
                    result="AWS_SECRET_KEY=abcdefghijklmnop")])])
    redact_conversation(conv)
    message = conv.messages[0]
    assert "AKIAIOSFODNN7EXAMPLE" not in message.text
    assert "AKIAIOSFODNN7EXAMPLE" not in message.thinking
    assert "ghp_ABCDEFGHIJ" not in message.tools[0].input["command"]
    assert "abcdefghijklmnop" not in message.tools[0].result
