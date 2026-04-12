from __future__ import annotations

import json
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore


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
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "paths": {
            "run_dir": str(run_dir),
            "runtime_dir": str(_runtime_root(runtime_dir)),
        },
        "state": state,
        "decision_log": _read_jsonl(run_dir / "decision_log.jsonl"),
        "session_log": _read_jsonl(run_dir / "session_log.jsonl"),
        "notes": related_notes(run_id, runtime_dir),
    }


def summarize_run(exported: dict) -> dict:
    session_log = exported.get("session_log", [])
    notes = exported.get("notes", [])
    event_counts = Counter(event.get("event_type", "") for event in session_log)
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
        },
        "oracle_consultation_ids": oracle_ids,
    }


def replay_run(exported: dict) -> dict:
    from supervisor.loop import SupervisorLoop

    state_data = exported.get("state") or {}
    spec_path = state_data.get("spec_path", "")
    if not spec_path:
        raise ValueError("exported run has no spec_path")
    spec = load_spec(spec_path)

    with tempfile.TemporaryDirectory(prefix="thin_supervisor_replay_") as tmpdir:
        store = StateStore(tmpdir)
        state = store.load_or_init(
            spec,
            spec_path=spec_path,
            pane_target=state_data.get("pane_target", ""),
            surface_type=state_data.get("surface_type", "tmux"),
            workspace_root=state_data.get("workspace_root", ""),
        )
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
                records.append({
                    "checkpoint_seq": payload.get("checkpoint_seq", 0),
                    "predicted": predicted,
                    "actual": actual,
                    "matched": matched,
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


def render_postmortem(exported: dict) -> str:
    summary = summarize_run(exported)
    counts = summary["counts"]
    oracle_text = ", ".join(summary["oracle_consultation_ids"]) or "(none)"
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
        f"- Notes: {counts['notes']}\n"
    )
