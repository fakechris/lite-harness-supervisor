from __future__ import annotations

import shlex
from typing import Any


def latest_human_escalation(state: dict[str, Any]) -> dict[str, Any]:
    escalations = state.get("human_escalations", []) or []
    if not escalations:
        return {}
    latest = escalations[-1]
    if isinstance(latest, dict):
        return latest
    return {"reason": str(latest)}


def pause_reason(state: dict[str, Any]) -> str:
    if state.get("top_state") != "PAUSED_FOR_HUMAN":
        return ""
    latest = latest_human_escalation(state)
    reason = latest.get("reason", "")
    return str(reason).strip()


def is_waiting_for_review(state: dict[str, Any]) -> bool:
    reason = pause_reason(state)
    return reason.startswith("requires review by:")


def status_reason(state: dict[str, Any]) -> str:
    top_state = state.get("top_state", "")
    if top_state == "PAUSED_FOR_HUMAN":
        return ""
    if top_state == "COMPLETED":
        return "workflow_done"
    current_node = str(state.get("current_node_id", "")).strip()
    if top_state == "RUNNING" and current_node:
        return f"working {current_node}"
    return ""


def next_action(state: dict[str, Any]) -> str:
    top_state = state.get("top_state")
    run_id = state.get("run_id", "")

    if top_state == "COMPLETED":
        if run_id:
            return f"thin-supervisor run summarize {run_id}"
        return ""

    if top_state != "PAUSED_FOR_HUMAN":
        return ""

    reason = pause_reason(state)
    if reason.startswith("requires review by:"):
        reviewer = reason.split(":", 1)[1].strip() or "human"
        if run_id:
            return f"thin-supervisor run review {run_id} --by {reviewer}"

    spec_path = state.get("spec_path", "")
    pane_target = state.get("pane_target", "")
    surface_type = state.get("surface_type", "")
    if spec_path and pane_target:
        command = (
            f"thin-supervisor run resume --spec {shlex.quote(spec_path)} "
            f"--pane {shlex.quote(pane_target)}"
        )
        if surface_type:
            command += f" --surface {shlex.quote(surface_type)}"
        return command

    if run_id:
        return f"thin-supervisor run summarize {run_id}"
    return ""


def summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    summary = dict(state)
    summary["pause_reason"] = pause_reason(state)
    summary["status_reason"] = status_reason(state)
    summary["next_action"] = next_action(state)
    summary["is_waiting_for_review"] = is_waiting_for_review(state)
    return summary
