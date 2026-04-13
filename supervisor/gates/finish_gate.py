"""Finish gate — enforces AcceptanceContract before allowing COMPLETED."""
from __future__ import annotations

import subprocess

from supervisor.domain.models import AcceptanceContract


class FinishGate:
    """Evaluates whether a run can transition to COMPLETED.

    Uses AcceptanceContract (if available) or falls back to FinishPolicy.
    """

    @staticmethod
    def _review_requirement_met(required: str, completed: set[str]) -> bool:
        if not required:
            return True
        if required == "human":
            return "human" in completed or "stronger_reviewer" in completed
        if required == "stronger_reviewer":
            return "stronger_reviewer" in completed
        return False

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
            evidence_strings = self._normalize_evidence_entries(provided)
            for req in contract.required_evidence:
                if not self._evidence_requirement_met(req, evidence_strings):
                    failures.append(f"missing required evidence: {req}")

        # Check must_review_by
        if contract.must_review_by:
            completed_reviews = set(getattr(state, "completed_reviews", []) or [])
            if not self._review_requirement_met(contract.must_review_by, completed_reviews):
                failures.append(f"requires review by: {contract.must_review_by}")

        return {
            "ok": len(failures) == 0,
            "reason": "; ".join(failures) if failures else "all acceptance criteria met",
            "failures": failures,
            "risk_class": contract.risk_class,
        }

    @staticmethod
    def _normalize_evidence_entries(provided) -> list[str]:
        entries: list[str] = []
        for item in provided:
            if isinstance(item, dict):
                parts = []
                for key, value in item.items():
                    key_text = " ".join(str(key).split()).lower()
                    value_text = " ".join(str(value).split()).lower()
                    if key_text and value_text:
                        parts.append(f"{key_text}: {value_text}")
                text = "; ".join(parts)
            else:
                text = " ".join(str(item).split()).lower()
            if text:
                entries.append(text)
        return entries

    @staticmethod
    def _evidence_requirement_met(requirement: str, entries: list[str]) -> bool:
        required = " ".join(str(requirement).split()).lower()
        if not required:
            return True
        if ":" in required:
            return any(
                entry == required or entry.startswith(f"{required};")
                for entry in entries
            )
        return any(required in entry for entry in entries)
