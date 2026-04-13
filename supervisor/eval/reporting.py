from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def default_report_dir(runtime_dir: str = ".supervisor/runtime") -> Path:
    return Path(runtime_dir).parent / "evals" / "reports"


def default_candidate_dir(runtime_dir: str = ".supervisor/runtime") -> Path:
    return Path(runtime_dir).parent / "evals" / "candidates"


def save_eval_report(
    payload: dict,
    *,
    report_kind: str,
    runtime_dir: str = ".supervisor/runtime",
    output_path: str = "",
) -> Path:
    path = Path(output_path) if output_path else _default_report_path(payload, report_kind, runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapped = {
        "report_kind": report_kind,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    path.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def save_candidate_manifest(
    payload: dict,
    *,
    runtime_dir: str = ".supervisor/runtime",
    output_path: str = "",
) -> Path:
    candidate = dict(payload.get("candidate") or {})
    candidate_id = str(candidate.get("candidate_id") or "").strip() or _candidate_id_from_payload(payload)
    candidate["candidate_id"] = candidate_id
    path = Path(output_path) if output_path else default_candidate_dir(runtime_dir) / f"{candidate_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapped = {
        "candidate_id": candidate_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "proposal": {
            "suite": payload.get("suite", ""),
            "objective": payload.get("objective", ""),
            "baseline_policy": payload.get("baseline_policy", ""),
            "recommended_candidate_policy": payload.get("recommended_candidate_policy", ""),
            "rationale": payload.get("rationale", ""),
        },
        "candidate": candidate,
    }
    path.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_candidate_manifest(
    *,
    candidate_id: str = "",
    manifest_path: str = "",
    runtime_dir: str = ".supervisor/runtime",
) -> dict:
    if manifest_path:
        path = Path(manifest_path)
    else:
        if not candidate_id:
            raise ValueError("candidate_id or manifest_path is required")
        path = default_candidate_dir(runtime_dir) / f"{candidate_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"candidate manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def review_candidate_manifest(manifest: dict) -> dict:
    candidate = dict(manifest.get("candidate") or {})
    proposal = dict(manifest.get("proposal") or {})
    candidate_id = str(manifest.get("candidate_id") or candidate.get("candidate_id") or "").strip()
    touched = candidate.get("touched_fragments") or []
    fragment_mutations = candidate.get("fragment_mutations") or []
    next_action = (
        f"thin-supervisor eval compare --suite {proposal.get('suite', 'approval-core')} "
        f"--candidate-policy {candidate.get('candidate_policy', '')}"
    ).strip()
    return {
        "candidate_id": candidate_id,
        "candidate_policy": candidate.get("candidate_policy", ""),
        "parent_id": candidate.get("parent_id", ""),
        "objective": proposal.get("objective", candidate.get("objective", "")),
        "suite": proposal.get("suite", ""),
        "review_status": "needs_human_review",
        "touched_fragments": touched,
        "mutation_operator": candidate.get("mutation_operator", ""),
        "fragment_mutation_count": len(fragment_mutations),
        "next_action": next_action,
        "rationale": proposal.get("rationale", ""),
    }


def _default_report_path(payload: dict, report_kind: str, runtime_dir: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    unique = uuid4().hex[:8]
    stem = (
        payload.get("suite")
        or payload.get("run_id")
        or payload.get("objective")
        or "report"
    )
    safe_stem = str(stem).replace("/", "-").replace(" ", "-")
    return default_report_dir(runtime_dir) / f"{timestamp}-{unique}-{report_kind}-{safe_stem}.json"


def _candidate_id_from_payload(payload: dict) -> str:
    basis = "|".join(
        [
            str(payload.get("suite", "")),
            str(payload.get("objective", "")),
            str(payload.get("baseline_policy", "")),
            str(payload.get("recommended_candidate_policy", "")),
        ]
    )
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]
    return f"candidate_{digest}"
