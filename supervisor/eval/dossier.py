from __future__ import annotations

from supervisor.eval.registry import current_promotions, list_promotions
from supervisor.eval.rollouts import current_rollouts, list_rollouts
from supervisor.eval.reporting import (
    list_eval_reports,
    load_candidate_manifest,
    review_candidate_manifest,
)


def build_candidate_dossier(
    *,
    candidate_id: str = "",
    manifest_path: str = "",
    runtime_dir: str = ".supervisor/runtime",
) -> dict:
    manifest = load_candidate_manifest(
        candidate_id=candidate_id,
        manifest_path=manifest_path,
        runtime_dir=runtime_dir,
    )
    review = review_candidate_manifest(manifest)
    candidate = dict(manifest.get("candidate") or {})
    proposal = dict(manifest.get("proposal") or {})
    reports = _related_reports(
        review=review,
        manifest=manifest,
        runtime_dir=runtime_dir,
    )

    history = [item for item in list_promotions(runtime_dir=runtime_dir) if item.get("candidate_id") == review["candidate_id"]]
    current = current_promotions(list_promotions(runtime_dir=runtime_dir)).get(review["suite"])
    is_current = bool(current and current.get("candidate_id") == review["candidate_id"])
    latest_gate = reports["latest"].get("gate")
    rollout_history = list_rollouts(runtime_dir=runtime_dir, candidate_id=review["candidate_id"])
    current_rollout = current_rollouts(rollout_history).get(review["candidate_id"], {})

    return {
        "candidate": candidate,
        "proposal": proposal,
        "review": review,
        "evidence": {
            "report_counts": reports["counts"],
            "reports": reports["reports"],
            "latest_reports": reports["latest"],
        },
        "promotion": {
            "history": history,
            "current_record": current if is_current else {},
            "is_current": is_current,
        },
        "rollouts": {
            "history": rollout_history,
            "current": current_rollout,
        },
        "next_action": _next_action(
            review=review,
            latest_gate=latest_gate,
            current_rollout=current_rollout,
            is_current=is_current,
        ),
    }


def _related_reports(*, review: dict, manifest: dict, runtime_dir: str) -> dict:
    candidate_id = review.get("candidate_id", "")
    candidate_policy = review.get("candidate_policy", "")
    suite = review.get("suite", "")

    related: list[dict] = []
    latest: dict[str, dict] = {}
    counts: dict[str, int] = {}

    for item in list_eval_reports(runtime_dir=runtime_dir):
        report_kind = str(item.get("report_kind", "")).strip()
        payload = dict(item.get("payload") or {})
        if not _is_related_report(
            candidate_id=candidate_id,
            candidate_policy=candidate_policy,
            suite=suite,
            manifest=manifest,
            report_kind=report_kind,
            payload=payload,
        ):
            continue

        summary = {
            "report_kind": report_kind,
            "saved_at": item.get("saved_at", ""),
            "path": item.get("path", ""),
        }
        related.append(summary)
        counts[report_kind] = counts.get(report_kind, 0) + 1
        previous = latest.get(report_kind)
        if previous is None or summary["saved_at"] >= previous.get("saved_at", ""):
            latest[report_kind] = {
                **summary,
                "payload": payload,
            }

    return {
        "reports": related,
        "counts": counts,
        "latest": latest,
    }


def _is_related_report(
    *,
    candidate_id: str,
    candidate_policy: str,
    suite: str,
    manifest: dict,
    report_kind: str,
    payload: dict,
) -> bool:
    payload_candidate = dict(payload.get("candidate") or {})

    if payload.get("candidate_id") == candidate_id:
        return True
    if payload_candidate.get("candidate_id") == candidate_id:
        return True
    if payload.get("suite") == suite and payload.get("candidate_policy") == candidate_policy:
        return True
    if report_kind == "proposal":
        proposal = dict(manifest.get("proposal") or {})
        return (
            payload.get("suite") == proposal.get("suite")
            and payload.get("objective") == proposal.get("objective")
            and payload.get("recommended_candidate_policy") == proposal.get("recommended_candidate_policy")
        )
    return False


def _next_action(*, review: dict, latest_gate: dict | None, current_rollout: dict, is_current: bool) -> str:
    if is_current:
        return "thin-supervisor-dev eval promotion-history --json"
    gate_saved_at = str((latest_gate or {}).get("saved_at", ""))
    rollout_saved_at = str(current_rollout.get("saved_at", ""))
    if latest_gate and gate_saved_at >= rollout_saved_at:
        payload = dict(latest_gate.get("payload") or {})
        if payload.get("next_action"):
            return str(payload["next_action"])
    if current_rollout:
        if current_rollout.get("decision") == "promote":
            return f"thin-supervisor-dev eval gate-candidate --candidate-id {review.get('candidate_id', '')} --run-id <recent_run>"
        if current_rollout.get("decision") in {"hold", "rollback"}:
            return f"thin-supervisor-dev eval review-candidate --candidate-id {review.get('candidate_id', '')}"
    if latest_gate:
        payload = dict(latest_gate.get("payload") or {})
        if payload.get("next_action"):
            return str(payload["next_action"])
    return str(review.get("next_action", ""))
