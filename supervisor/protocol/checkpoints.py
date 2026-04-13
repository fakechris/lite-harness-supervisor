from __future__ import annotations

import re


CHECKPOINT_ALLOWED_STATUSES = {
    "working",
    "blocked",
    "step_done",
    "workflow_done",
}

CHECKPOINT_STRING_MAX = 512
CHECKPOINT_LIST_ITEM_MAX = 300
CHECKPOINT_LIST_MAX = 20
CHECKPOINT_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
CHECKPOINT_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")


def checkpoint_example_block(node_id: str) -> str:
    return (
        "<checkpoint>\n"
        "run_id: <run_id>\n"
        "checkpoint_seq: <incrementing integer>\n"
        "status: <working | blocked | step_done | workflow_done>\n"
        f"current_node: {node_id}\n"
        "summary: <one-line description>\n"
        "evidence:\n"
        "  - modified: <file path>\n"
        "  - ran: <command>\n"
        "  - result: <short result>\n"
        "candidate_next_actions:\n"
        "  - <next action>\n"
        "needs:\n"
        "  - none\n"
        "question_for_supervisor:\n"
        "  - none\n"
        "</checkpoint>"
    )


def sanitize_instruction_text(text: str | None, *, max_chars: int = 1200) -> str | None:
    if not text:
        return None
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return None
    lowered = cleaned.lower()
    forbidden_markers = ("<checkpoint>", "</checkpoint>", "<decision>", "</decision>")
    if any(marker in lowered for marker in forbidden_markers):
        return None
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip()
    return cleaned


def sanitize_checkpoint_payload(raw: dict, *, fallback_run_id: str = "", fallback_surface_id: str = "") -> dict | None:
    status = str(raw.get("status", "")).strip().lower()
    current_node = str(raw.get("current_node", "")).strip()
    if status not in CHECKPOINT_ALLOWED_STATUSES:
        return None
    if not current_node or not CHECKPOINT_NODE_ID_RE.match(current_node):
        return None

    run_id = str(raw.get("run_id", "")).strip()
    if run_id and not CHECKPOINT_RUN_ID_RE.match(run_id):
        run_id = ""

    summary = _sanitize_text(raw.get("summary", ""), max_len=CHECKPOINT_STRING_MAX)
    checkpoint_seq = _sanitize_int(raw.get("checkpoint_seq", 0))
    surface_id = _sanitize_text(raw.get("surface_id", ""), max_len=128) or fallback_surface_id

    return {
        "status": status,
        "current_node": current_node,
        "summary": summary,
        "run_id": run_id or fallback_run_id,
        "checkpoint_seq": checkpoint_seq,
        "surface_id": surface_id,
        "evidence": _sanitize_list(raw.get("evidence", [])),
        "candidate_next_actions": _sanitize_list(raw.get("candidate_next_actions", [])),
        "needs": _sanitize_list(raw.get("needs", [])),
        "question_for_supervisor": _sanitize_list(raw.get("question_for_supervisor", [])),
    }


def _sanitize_text(value, *, max_len: int) -> str:
    text = " ".join(str(value).split())
    if not text:
        return ""
    return text[:max_len].rstrip()


def _sanitize_int(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _sanitize_list(values) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    items: list[str] = []
    for value in values:
        text = _normalize_list_item(value)
        if text:
            items.append(text)
        if len(items) >= CHECKPOINT_LIST_MAX:
            break
    return items


def _normalize_list_item(value) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            normalized_key = _sanitize_text(key, max_len=64)
            normalized_value = _sanitize_text(item, max_len=CHECKPOINT_LIST_ITEM_MAX)
            if normalized_key and normalized_value:
                parts.append(f"{normalized_key}: {normalized_value}")
        return "; ".join(parts)
    return _sanitize_text(value, max_len=CHECKPOINT_LIST_ITEM_MAX)
