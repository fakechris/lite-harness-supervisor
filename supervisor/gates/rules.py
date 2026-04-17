from __future__ import annotations
import re

SOFT_CONFIRMATION_PATTERNS = [
    r"要不要我继续",
    r"如果你同意",
    r"接下来我可以",
    r"是否继续",
    r"say go",
    r"keep driving",
    r"next I can",
]

MISSING_EXTERNAL_INPUT_PATTERNS = [
    r"需要你提供",
    r"缺少.*(账号|密钥|token|权限|文件|图片|截图|链接)",
    r"need.*(access|credentials|input)",
    r"waiting for.*(access|credentials|input|token|approval|permission)",
]

DANGEROUS_ACTION_PATTERNS = [
    r"delete production",
    r"drop table",
    r"force push",
    r"永久删除",
    r"不可逆",
]

BLOCKED_PATTERNS = [
    r"\bblocked\b",
    r"cannot proceed",
    r"无法继续",
    r"等待.*输入",
]

def classify_text(text: str) -> str | None:
    if not text:
        return None
    for pattern in SOFT_CONFIRMATION_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            return "SOFT_CONFIRMATION"
    for pattern in MISSING_EXTERNAL_INPUT_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            return "MISSING_EXTERNAL_INPUT"
    for pattern in DANGEROUS_ACTION_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            return "DANGEROUS_ACTION"
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            return "BLOCKED"
    return None

# Concrete markers of execution work on the *current node's objective*.
#
# This list is intentionally conservative: false positives on RE_INJECT are
# cheap (one extra inject, retry budget untouched), false negatives
# (admin-only checkpoint that slips through to CONTINUE) are the Phase 17
# failure.  So we require a *specific* signal — test runner invocation,
# test/build output, diff markers, or completion verbs tied to the work
# itself — not generic words like "ran" or "modified" that routinely
# show up in admin activity (e.g. `ran: git status`, `modified:
# .supervisor/specs/foo.yaml`).
EXECUTION_EVIDENCE_PATTERNS = [
    # Test / build runner invocation
    r"\bpytest\b",
    r"\bunittest\b",
    r"\bjest\b",
    r"\bcargo\s+test\b",
    r"\bgo\s+test\b",
    r"\bnpm\s+(test|run)\b",
    r"\bmake\s+(test|build|check)\b",
    r"\btox\b",
    # Runner output / exit signals
    r"\btests?\s+pass(ed|es)?\b",
    r"\btests?\s+fail(ed)?\b",
    r"\b\d+\s+passed\b",
    r"\b\d+\s+failed\b",
    r"\bbuild\s+(succeeded|failed|complete)\b",
    r"\bcompiled\b",
    r"\bexit\s+code\s+\d",
    r"\btraceback\b",
    # Real diff markers
    r"\bdiff\s+--git\b",
    r"^\+\+\+\s",
    r"^---\s",
    # Verifier / harness signals
    r"\bverifier\b",
    r"\bverified\b",
    r"\bharness\b",
    # Commit / merge completion verbs (distinct from 'git status')
    r"\bgit\s+commit\b",
    r"\bcommitted\b",
    r"\bmerged\b",
    # Implementation / fix verbs tied to the work (not to planning)
    r"\bimplement(ed|ation)\b",
    r"\bfixed\b",
    r"\brefactor(ed|ing)\b",
]


def is_admin_only_evidence(evidence) -> bool:
    """Returns True if a checkpoint's `evidence` has no concrete execution signal.

    "Admin-only" means the agent cited attachment / clarify / plan / spec /
    baseline artifacts (or side-work like `git status` / spec edits) but
    has not yet produced work on the node's objective.  Enforced at the
    attach boundary so a CONTINUE on attach cannot advance a run that
    hasn't actually started executing the current node.

    Default is admin-only.  We only escape to "real work" when at least
    one evidence item carries a specific execution marker from
    `EXECUTION_EVIDENCE_PATTERNS`.  Generic verbs like "ran" or "modified"
    do not qualify on their own: they occur as often in admin activity
    (git status, spec editing) as in real work, and the cost of one
    unnecessary RE_INJECT is strictly lower than the cost of a Phase-17
    false advance.
    """
    if not evidence:
        return True
    for item in evidence:
        text = ""
        if isinstance(item, dict):
            parts = []
            for key, value in item.items():
                key_text = " ".join(str(key).split())
                value_text = " ".join(str(value).split())
                parts.append(f"{key_text}: {value_text}")
            text = " ".join(parts)
        else:
            text = str(item)
        if not text.strip():
            continue
        if any(re.search(p, text, flags=re.I | re.M) for p in EXECUTION_EVIDENCE_PATTERNS):
            return False
    return True


def classify_checkpoint(checkpoint: dict) -> str | None:
    status = checkpoint.get("status")
    summary = checkpoint.get("summary", "")
    needs = checkpoint.get("needs", [])
    question = checkpoint.get("question_for_supervisor", [])
    evidence = checkpoint.get("evidence", [])

    evidence_parts: list[str] = []
    for item in evidence:
        if isinstance(item, dict):
            parts = []
            for key, value in item.items():
                key_text = " ".join(str(key).split())
                value_text = " ".join(str(value).split())
                if key_text and value_text:
                    parts.append(f"{key_text}: {value_text}")
            if parts:
                evidence_parts.append("; ".join(parts))
        else:
            evidence_parts.append(str(item))

    joined = " ".join(
        [
            str(status),
            str(summary),
            " ".join(map(str, needs)),
            " ".join(map(str, question)),
            " ".join(evidence_parts),
        ]
    )
    return classify_text(joined)
