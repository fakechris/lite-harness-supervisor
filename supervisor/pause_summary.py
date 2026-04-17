from __future__ import annotations

import shlex
from typing import Any


PAUSE_CLASSES = ("business", "safety", "review", "recovery")


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


def pause_class(state: dict[str, Any]) -> str:
    """Return `pause_class` for the latest escalation, or "" if none/unknown.

    `pause_class` is set by `_pause_for_human` in the runtime; surfaces rely on it
    to distinguish business/safety/review from recovery so operators can tell
    "I'm blocked on your input" from "supervisor recovery exhausted" without
    parsing `reason` strings.
    """
    if state.get("top_state") != "PAUSED_FOR_HUMAN":
        return ""
    latest = latest_human_escalation(state)
    value = str(latest.get("pause_class", "")).strip().lower()
    return value if value in PAUSE_CLASSES else ""


def is_waiting_for_review(state: dict[str, Any]) -> bool:
    if pause_class(state) == "review":
        return True
    reason = pause_reason(state)
    return reason.startswith("requires review by:")


def status_reason(state: dict[str, Any]) -> str:
    top_state = state.get("top_state", "")
    if top_state == "PAUSED_FOR_HUMAN":
        delivery = state.get("delivery_state", "IDLE")
        if delivery == "FAILED":
            return "delivery_failed"
        if delivery == "TIMED_OUT":
            return "delivery_timed_out"
        return ""
    if top_state == "RECOVERY_NEEDED":
        return "supervisor_recovering"
    if top_state == "COMPLETED":
        return "workflow_done"
    current_node = str(state.get("current_node_id", "")).strip()
    if top_state == "RUNNING" and current_node:
        delivery = state.get("delivery_state", "IDLE")
        controller = state.get("controller_mode", "daemon")
        prefix = "debug foreground" if controller == "foreground" else "working"
        if delivery in ("INJECTED", "SUBMITTED"):
            return f"delivering instruction to {current_node}"
        if delivery == "ACKNOWLEDGED":
            return f"agent acknowledged, awaiting checkpoint for {current_node}"
        return f"{prefix} {current_node}"
    return ""


def next_action(state: dict[str, Any]) -> str:
    top_state = state.get("top_state")
    run_id = state.get("run_id", "")

    if top_state == "COMPLETED":
        if run_id:
            return f"thin-supervisor run summarize {run_id}"
        return ""

    # Supervisor is actively auto-recovering — operator shouldn't act yet, but
    # if they're watching, `inspect` is the right command if it persists.
    if top_state == "RECOVERY_NEEDED":
        if run_id:
            return f"thin-supervisor inspect {run_id} --if-persists"
        return ""

    if top_state != "PAUSED_FOR_HUMAN":
        return ""

    reason = pause_reason(state)
    pclass = pause_class(state)

    # Review pauses: reviewer is named in the reason, not the class.
    if reason.startswith("requires review by:"):
        reviewer = reason.split(":", 1)[1].strip() or "human"
        if run_id:
            return f"thin-supervisor run review {run_id} --by {reviewer}"

    spec_path = state.get("spec_path", "")
    pane_target = state.get("pane_target", "")
    surface_type = state.get("surface_type", "")

    # Recovery pauses mean "supervisor tried and failed to advance the run."
    # The operator's first move is inspection, not a blind resume — resuming
    # without diagnosing the pane can loop right back into the same fault.
    if pclass == "recovery" and run_id:
        return f"thin-supervisor inspect {run_id}"

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
    summary["pause_class"] = pause_class(state)
    summary["status_reason"] = status_reason(state)
    summary["next_action"] = next_action(state)
    summary["is_waiting_for_review"] = is_waiting_for_review(state)
    return summary
