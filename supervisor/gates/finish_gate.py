"""Finish gate — enforces AcceptanceContract before allowing COMPLETED."""
from __future__ import annotations

import subprocess

from supervisor.domain.models import AcceptanceContract


class FinishGate:
    """Evaluates whether a run can transition to COMPLETED.

    Uses AcceptanceContract (if available) or falls back to FinishPolicy.
    """

    def evaluate(self, spec, state, *, cwd: str | None = None) -> dict:
        contract = spec.acceptance
        if contract is None:
            # Backward compat: build from finish_policy
            contract = AcceptanceContract.from_finish_policy(spec.finish_policy, goal=spec.goal)

        failures: list[str] = []

        # Check all steps done
        if contract.require_all_steps_done:
            if spec.kind == "conditional_workflow":
                required = set(state.done_node_ids)
                required.add(state.current_node_id)
            else:
                required = {n.id for n in spec.ordered_nodes()}
            done = set(state.done_node_ids)
            missing = required - done
            if missing:
                failures.append(f"nodes not done: {', '.join(sorted(missing))}")

        # Check verification pass
        if contract.require_verification_pass:
            v = state.verification or {}
            if not v.get("ok", False) and v.get("last_status") != "pending":
                failures.append("last verification did not pass")

        # Check git cleanliness (once, reused for both clean_repo and forbidden state)
        git_dirty: bool | None = None
        needs_git = (
            contract.require_clean_or_committed_repo
            or "uncommitted_changes" in contract.forbidden_states
        )
        if needs_git:
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True, text=True, timeout=10, cwd=cwd,
                )
                git_dirty = bool(result.stdout.strip())
            except (subprocess.SubprocessError, FileNotFoundError):
                git_dirty = None  # unknown

        if contract.require_clean_or_committed_repo:
            if git_dirty is True:
                failures.append("repo has uncommitted changes")
            elif git_dirty is None:
                failures.append("could not check git status")

        # Check forbidden states
        for forbidden in contract.forbidden_states:
            if forbidden == "test_failing":
                v = state.verification or {}
                if not v.get("ok", True):
                    failures.append(f"forbidden state: {forbidden}")
            elif forbidden == "uncommitted_changes":
                if git_dirty is True:
                    failures.append(f"forbidden state: {forbidden}")

        # Check required_evidence against checkpoint evidence
        if contract.required_evidence:
            cp = state.last_agent_checkpoint or {}
            provided = cp.get("evidence", [])
            # Flatten evidence items to searchable strings
            evidence_strings: list[str] = []
            for e in provided:
                if isinstance(e, dict):
                    evidence_strings.extend(str(v) for v in e.values())
                else:
                    evidence_strings.append(str(e))
            evidence_text = " ".join(evidence_strings).lower()
            for req in contract.required_evidence:
                if req.lower() not in evidence_text:
                    failures.append(f"missing required evidence: {req}")

        # Check must_review_by
        if contract.must_review_by:
            failures.append(f"requires review by: {contract.must_review_by}")

        return {
            "ok": len(failures) == 0,
            "reason": "; ".join(failures) if failures else "all acceptance criteria met",
            "failures": failures,
            "risk_class": contract.risk_class,
        }
