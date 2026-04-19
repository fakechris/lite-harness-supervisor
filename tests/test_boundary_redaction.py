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


def test_redacts_openai_project_key():
    """OpenAI's project-scoped keys (``sk-proj-...``), service-account
    keys (``sk-svcacct-...``), and admin keys (``sk-admin-...``) all
    carry hyphens and underscores in their body.  The earlier classic-
    only rule stopped at the first ``-`` and left the real secret
    visible from the second segment onward."""
    for prefix in ("sk-proj-", "sk-svcacct-", "sk-admin-"):
        token = prefix + "Abc123_defGHI456-jklMNO789"
        out = redact(f"key={token} rest")
        assert token not in out, prefix
        assert "[REDACTED:api_key]" in out, prefix


def test_does_not_over_redact_short_sk_prose():
    """``sk-`` prose that is clearly too short to be a key passes
    through — we need enough body characters to avoid matching
    innocuous strings like ``sk-foo``."""
    out = redact("commit sk-ok done")
    assert out == "commit sk-ok done"


def test_redacts_slack_token():
    out = redact("token=xoxb-1234567890-abcdefg-xyz")
    assert "xoxb-1234567890-abcdefg-xyz" not in out
    assert "[REDACTED:slack_token]" in out


def test_redacts_github_token():
    out = redact("auth ghp_abcdefghijklmnopqrstuvwxyz1234567890 here")
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in out
    assert "[REDACTED:github_token]" in out


def test_redacts_all_github_short_prefixes():
    """GitHub ships five short-prefix token families (ghp_, gho_, ghu_,
    ghs_, ghr_) — each is a distinct credential type and all must be
    redacted, not just the classic PAT prefix."""
    tail = "abcdefghijklmnopqrstuvwxyz1234567890"
    for prefix in ("ghp", "gho", "ghu", "ghs", "ghr"):
        token = f"{prefix}_{tail}"
        out = redact(f"token {token} end")
        assert token not in out, prefix
        assert "[REDACTED:github_token]" in out, prefix


def test_redacts_github_fine_grained_pat():
    """``github_pat_`` is GitHub's fine-grained PAT prefix — longer than
    the short-prefix family and must match before ``ghp_`` swallows it."""
    token = "github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz1234567890ABCDEFG"
    out = redact(f"auth {token} here")
    assert token not in out
    assert "[REDACTED:github_fine_pat]" in out


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
