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
    r"waiting for",
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

def classify_checkpoint(checkpoint: dict) -> str | None:
    status = checkpoint.get("status")
    needs = checkpoint.get("needs", [])
    question = checkpoint.get("question_for_supervisor", [])

    joined = " ".join([str(status), " ".join(map(str, needs)), " ".join(map(str, question))])
    return classify_text(joined)
