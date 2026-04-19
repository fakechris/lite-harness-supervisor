"""Outbound redaction — scrub API keys / tokens / JWT from text before
it is persisted or forwarded.

Patterns are ordered: JWT (most specific) → provider-prefixed tokens →
AWS → email. Generic "bearer <opaque>" is not redacted to avoid false
positives on legitimate auth flow discussion; we only redact tokens
that are self-identifying via prefix or structure.

``redact_emails`` defaults to False because emails are frequently
legitimate context (PR reviewer, commit author, contact info).
"""
from __future__ import annotations

import re

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("jwt", re.compile(r"\bey[A-Za-z0-9_-]{10,}\.ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\b")),
    ("github_token", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def redact(text: str, *, redact_emails: bool = False) -> str:
    if not text:
        return text
    out = text
    for kind, pattern in _RULES:
        out = pattern.sub(f"[REDACTED:{kind}]", out)
    if redact_emails:
        out = _EMAIL.sub("[REDACTED:email]", out)
    return out
