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

# Markers of concrete execution work on the current node.  If ANY evidence
# item carries one of these, the checkpoint is treated as real progress —
# not an admin-only artifact.  Used by the ATTACHED-state gate to decide
# whether to RE_INJECT a first-execution prompt.
EXECUTION_EVIDENCE_PATTERNS = [
    r"\bran\b",
    r"\bexecuted\b",
    r"\bcommand\b",
    r"\bmodified\b",
    r"\bedited\b",
    r"\bwrote\b",
    r"\bcreated\b",
    r"\bdeleted\b",
    r"\bchanged\b",
    r"\bfile\b",
    r"\bdiff\b",
    r"\boutput\b",
    r"\bstdout\b",
    r"\bstderr\b",
    r"\bverifier?\b",
    r"\bverified\b",
    r"\btest pass(ed|es)\b",
]


def is_admin_only_evidence(evidence) -> bool:
    """Returns True if a checkpoint's `evidence` has no concrete execution signal.

    "Admin-only" means the agent cited attachment / clarify / plan / spec /
    baseline artifacts but has not yet produced work on the node's objective.
    Enforced at the attach boundary so a CONTINUE on attach cannot advance a
    run that hasn't actually started executing the current node.
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
        if any(re.search(p, text, flags=re.I) for p in EXECUTION_EVIDENCE_PATTERNS):
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
