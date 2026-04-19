"""Tests for outbound redaction.

Contract:
- ``redact(text, redact_emails=False) -> str`` returns a copy with
  API keys / bearer tokens / JWT / AWS keys replaced by
  ``[REDACTED:<kind>]``.
- Emails are preserved by default (legitimate context in most agent
  conversations) unless ``redact_emails=True``.
- Multiple occurrences on one line are all replaced.
- Empty string passes through unchanged.
"""
from __future__ import annotations

from supervisor.boundary.redaction import redact


def test_redacts_openai_style_key():
    out = redact("key is sk-ABCDEFGHIJ0123456789XYZ and more text")
    assert "sk-ABCDEFGHIJ0123456789XYZ" not in out
    assert "[REDACTED:api_key]" in out


def test_redacts_slack_token():
    out = redact("token=xoxb-1234567890-abcdefg-xyz")
    assert "xoxb-1234567890-abcdefg-xyz" not in out
    assert "[REDACTED:slack_token]" in out


def test_redacts_github_token():
    out = redact("auth ghp_abcdefghijklmnopqrstuvwxyz1234567890 here")
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in out
    assert "[REDACTED:github_token]" in out


def test_redacts_aws_access_key():
    out = redact("aws=AKIAIOSFODNN7EXAMPLE")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:aws_key]" in out


def test_redacts_jwt():
    jwt = "eyJhbGciOiJIUzI1NiIs.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    out = redact(f"bearer {jwt}")
    assert jwt not in out
    assert "[REDACTED:jwt]" in out


def test_emails_preserved_by_default():
    out = redact("contact alice@example.com for details")
    assert "alice@example.com" in out


def test_emails_redacted_when_opted_in():
    out = redact("contact alice@example.com", redact_emails=True)
    assert "alice@example.com" not in out
    assert "[REDACTED:email]" in out


def test_multiple_occurrences_all_replaced():
    text = "k1 sk-AAAAAAAAAAAAAAAAAAAAAA and k2 sk-BBBBBBBBBBBBBBBBBBBBBB"
    out = redact(text)
    assert "sk-A" not in out
    assert "sk-B" not in out
    assert out.count("[REDACTED:api_key]") == 2


def test_empty_string_passes_through():
    assert redact("") == ""


def test_benign_text_unchanged():
    s = "please review PR #42 and leave comments"
    assert redact(s) == s
