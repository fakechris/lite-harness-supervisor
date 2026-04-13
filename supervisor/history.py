from __future__ import annotations

import json
import hashlib
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.learning import list_friction_events, load_user_preferences


SCHEMA_VERSION = "run_export.v1"


def _runtime_root(runtime_dir: str = ".supervisor/runtime") -> Path:
    return Path(runtime_dir)


def _run_dir(run_id: str, runtime_dir: str = ".supervisor/runtime") -> Path:
    path = _runtime_root(runtime_dir) / "runs" / run_id
    if not path.exists():
        raise FileNotFoundError(f"run {run_id} not found at {path}")
    return path


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return items


def _shared_notes_path(runtime_dir: str = ".supervisor/runtime") -> Path:
    return _runtime_root(runtime_dir) / "shared" / "notes.jsonl"


def related_notes(run_id: str, runtime_dir: str = ".supervisor/runtime") -> list[dict]:
    notes = _read_jsonl(_shared_notes_path(runtime_dir))
    return [note for note in notes if note.get("author_run_id") == run_id]


def _spec_snapshot_from_state(state: dict) -> dict:
    spec_path = state.get("spec_path", "")
    if not spec_path:
        return {"path": "", "content": "", "hash": ""}
    path = Path(spec_path)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        content = ""
    snapshot_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16] if content else ""
    return {
        "path": str(path),
        "content": content,
        "hash": snapshot_hash,
    }


def latest_oracle_consultation_id_for_run(run_id: str, runtime_dir: str = ".supervisor/runtime") -> str:
    consultation_id = ""
    for note in related_notes(run_id, runtime_dir):
        if note.get("note_type") != "oracle":
            continue
        metadata = note.get("metadata") or {}
        cid = metadata.get("consultation_id", "")
        if cid:
            consultation_id = cid
    return consultation_id


def export_run(run_id: str, runtime_dir: str = ".supervisor/runtime") -> dict:
    run_dir = _run_dir(run_id, runtime_dir)
    state = _read_json(run_dir / "state.json", {})
    spec_snapshot = _spec_snapshot_from_state(state)
    user_id = "default"
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "paths": {
            "run_dir": str(run_dir),
            "runtime_dir": str(_runtime_root(runtime_dir)),
        },
        "state": state,
        "spec_snapshot": spec_snapshot,
        "decision_log": _read_jsonl(run_dir / "decision_log.jsonl"),
        "session_log": _read_jsonl(run_dir / "session_log.jsonl"),
        "notes": related_notes(run_id, runtime_dir),
        "friction_events": list_friction_events(runtime_dir, run_id=run_id, user_id=user_id),
        "user_preferences": load_user_preferences(runtime_dir, user_id=user_id),
    }


def summarize_run(exported: dict) -> dict:
    session_log = exported.get("session_log", [])
    notes = exported.get("notes", [])
    friction_events = exported.get("friction_events", [])
    event_counts = Counter(event.get("event_type", "") for event in session_log)
    friction_kinds = sorted({event.get("kind", "") for event in friction_events if event.get("kind", "")})
    verification_ok = 0
    verification_failed = 0
    oracle_ids: list[str] = []
    seen_ids: set[str] = set()
    for event in session_log:
        if event.get("event_type") == "verification":
            ok = bool((event.get("payload") or {}).get("ok"))
            if ok:
                verification_ok += 1
            else:
                verification_failed += 1
        if event.get("event_type") == "routing":
            cid = (event.get("payload") or {}).get("consultation_id", "")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                oracle_ids.append(cid)
    for note in notes:
        metadata = note.get("metadata") or {}
        cid = metadata.get("consultation_id", "")
        if cid and cid not in seen_ids:
            seen_ids.add(cid)
            oracle_ids.append(cid)

    return {
        "run_id": exported.get("run_id", ""),
        "spec_id": (exported.get("state") or {}).get("spec_id", ""),
        "top_state": (exported.get("state") or {}).get("top_state", "UNKNOWN"),
        "current_node": (exported.get("state") or {}).get("current_node_id", ""),
        "counts": {
            "checkpoints": event_counts.get("checkpoint", 0),
            "gate_decisions": event_counts.get("gate_decision", 0),
            "verifications": event_counts.get("verification", 0),
            "verifications_ok": verification_ok,
            "verifications_failed": verification_failed,
            "routing_events": event_counts.get("routing", 0),
            "review_acknowledged": event_counts.get("review_acknowledged", 0),
            "notes": len(notes),
            "oracle_notes": sum(1 for note in notes if note.get("note_type") == "oracle"),
            "friction_events": len(friction_events),
        },
        "oracle_consultation_ids": oracle_ids,
        "friction_kinds": friction_kinds,
    }


def replay_run(exported: dict) -> dict:
    from supervisor.loop import SupervisorLoop

    state_data = exported.get("state") or {}
    spec_path = state_data.get("spec_path", "")
    spec_snapshot = exported.get("spec_snapshot") or {}
    snapshot_content = spec_snapshot.get("content", "")
    snapshot_path = spec_snapshot.get("path", spec_path)
    if not spec_path and not snapshot_path:
        raise ValueError("exported run has no spec_path")

    with tempfile.TemporaryDirectory(prefix="thin_supervisor_replay_") as tmpdir:
        replay_spec_path = Path(tmpdir) / Path(snapshot_path or spec_path).name
        if snapshot_content:
            replay_spec_path.write_text(snapshot_content, encoding="utf-8")
        else:
            replay_spec_path.write_text(Path(spec_path).read_text(encoding="utf-8"), encoding="utf-8")
        spec = load_spec(str(replay_spec_path))
        store = StateStore(tmpdir, runtime_root=exported.get("paths", {}).get("runtime_dir"))
        state = store.load_or_init(
            spec,
            spec_path=str(replay_spec_path),
            pane_target=state_data.get("pane_target", ""),
            surface_type=state_data.get("surface_type", "tmux"),
            workspace_root=state_data.get("workspace_root", ""),
        )
        state.run_id = state_data.get("run_id", exported.get("run_id", state.run_id))
        loop = SupervisorLoop(store)
        actual_decisions = [
            (event.get("payload") or {})
            for event in exported.get("session_log", [])
            if event.get("event_type") == "gate_decision"
        ]
        actual_index = 0
        records: list[dict] = []
        for event in exported.get("session_log", []):
            event_type = event.get("event_type")
            payload = event.get("payload") or {}
            if event_type == "checkpoint":
                loop.handle_event(state, {"type": "agent_output", "payload": {"checkpoint": payload}})
                predicted = loop.gate(
                    spec,
                    state,
                    triggered_by_seq=payload.get("checkpoint_seq", 0),
                    triggered_by_checkpoint_id=payload.get("checkpoint_id", ""),
                ).to_dict()
                actual = actual_decisions[actual_index] if actual_index < len(actual_decisions) else {}
                actual_index += 1
                matched = (
                    predicted.get("decision") == actual.get("decision")
                    and predicted.get("gate_type") == actual.get("gate_type")
                    and predicted.get("next_node_id") == actual.get("next_node_id")
                    and predicted.get("selected_branch") == actual.get("selected_branch")
                    and predicted.get("needs_human") == actual.get("needs_human")
                )
                mismatch_kind = _classify_replay_divergence(predicted, actual) if not matched else ""
                records.append({
                    "checkpoint_seq": payload.get("checkpoint_seq", 0),
                    "predicted": predicted,
                    "actual": actual,
                    "matched": matched,
                    "mismatch_kind": mismatch_kind,
                })
                loop.apply_decision(spec, state, predicted)
            elif event_type == "verification":
                loop.apply_verification(spec, state, payload, cwd=state.workspace_root)

    mismatches = [record for record in records if not record["matched"]]
    return {
        "run_id": exported.get("run_id", ""),
        "decision_count": len(records),
        "matched_count": len(records) - len(mismatches),
        "mismatches": mismatches,
        "records": records,
    }


def _classify_replay_divergence(predicted: dict, actual: dict) -> str:
    predicted_decision = predicted.get("decision", "")
    actual_decision = actual.get("decision", "")
    predicted_needs_human = bool(predicted.get("needs_human", False))
    actual_needs_human = bool(actual.get("needs_human", False))

    if predicted_needs_human != actual_needs_human:
        return "safety_regression"
    if {
        predicted_decision,
        actual_decision,
    } & {"ESCALATE_TO_HUMAN", "ABORT"} and predicted_decision != actual_decision:
        return "safety_regression"
    if predicted_decision == actual_decision and predicted.get("gate_type") != actual.get("gate_type"):
        return "equivalent_divergence"
    if (
        predicted.get("next_node_id") != actual.get("next_node_id")
        or predicted.get("selected_branch") != actual.get("selected_branch")
    ):
        return "risky_routing_divergence"
    return "ux_only_divergence"


def render_postmortem(exported: dict) -> str:
    summary = summarize_run(exported)
    counts = summary["counts"]
    oracle_text = ", ".join(summary["oracle_consultation_ids"]) or "(none)"
    friction_text = ", ".join(summary.get("friction_kinds", [])) or "(none)"
    return (
        f"# Run Postmortem: {summary['run_id']}\n\n"
        f"- Top state: `{summary['top_state']}`\n"
        f"- Spec: `{summary['spec_id']}`\n"
        f"- Current node: `{summary['current_node']}`\n"
        f"- Checkpoints: {counts['checkpoints']}\n"
        f"- Gate decisions: {counts['gate_decisions']}\n"
        f"- Verifications ok: {counts['verifications_ok']}\n"
        f"- Verifications failed: {counts['verifications_failed']}\n"
        f"- Routing events: {counts['routing_events']}\n"
        f"- Oracle consultations: `{oracle_text}`\n"
        f"- Friction events: {counts['friction_events']}\n"
        f"- Friction kinds: `{friction_text}`\n"
        f"- Notes: {counts['notes']}\n"
    )
