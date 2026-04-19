"""Pattern-based injection scan on inbound text.

Deliberately narrow: we catch payload-shape garbage (role reassignment,
template escapes, obvious eval / XSS reflections, system-prompt leak
probes). This is NOT a model-level jailbreak filter — adaptive attackers
will bypass it. Its purpose is to keep clearly malformed / clearly
hostile payloads out of the session mailbox so operators don't see them.

Patterns are lifted and trimmed from ``iamagenius00/hermes-a2a``.
"""
from __future__ import annotations

import re

from .models import GuardResult

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_previous", re.compile(r"ignore\s+(the\s+)?previous\s+instructions", re.IGNORECASE)),
    ("disregard_above", re.compile(r"disregard\s+(the\s+)?above", re.IGNORECASE)),
    ("role_reassignment", re.compile(r"you\s+are\s+now\b", re.IGNORECASE)),
    ("template_escape", re.compile(r"(</system>|<\|im_start\|>\s*system)", re.IGNORECASE)),
    ("code_execution", re.compile(r"execute\s+the\s+following\s+code", re.IGNORECASE)),
    ("html_script", re.compile(r"<\s*script[\s>]", re.IGNORECASE)),
    ("js_protocol", re.compile(r"javascript\s*:", re.IGNORECASE)),
    ("system_prompt_leak", re.compile(r"(show\s+your\s+system\s+prompt|repeat\s+the\s+above)", re.IGNORECASE)),
)


def scan(text: str) -> GuardResult:
    if not text:
        return GuardResult(ok=True, stage="", reason="", normalized_text=text)
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return GuardResult(
                ok=False, stage="injection", reason=f"pattern:{name}", normalized_text=""
            )
    return GuardResult(ok=True, stage="", reason="", normalized_text=text)
