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
    # GitHub ships six token prefixes (ghp_ classic PAT, github_pat_ fine-
    # grained PAT, gho_ OAuth, ghu_ user-to-server, ghs_ server-to-server,
    # ghr_ refresh).  Match the fine-grained one first (it is longer) so
    # the short-prefix rule does not swallow a shared substring.
    ("github_fine_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # Modern OpenAI keys come in several sub-family prefixes after the
    # ``sk-`` sentinel (``sk-proj-...`` for project-scoped, ``sk-svcacct-...``
    # for service accounts, ``sk-admin-...`` for admin, plus the legacy
    # ``sk-<alnum>`` classic form).  All use hyphens and underscores in
    # their body, so the classic ``sk-[A-Za-z0-9]{20,}`` rule stopped at
    # the first ``-`` and left the real key visible from the second
    # segment onward.  Allow ``[A-Za-z0-9_-]`` throughout and require
    # enough length to avoid matching innocuous "sk-foo-bar" prose.
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{19,}\b")),
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
