"""Finish gate — enforces finish_policy before allowing COMPLETED."""
from __future__ import annotations

import subprocess


class FinishGate:
    """Evaluates whether a run can transition to COMPLETED.

    Checks finish_policy requirements:
    - require_all_steps_done
    - require_verification_pass
    - require_clean_or_committed_repo
    """

    def evaluate(self, spec, state, *, cwd: str | None = None) -> dict:
        failures: list[str] = []
        fp = spec.finish_policy

        if fp.require_all_steps_done:
            required = {n.id for n in spec.ordered_nodes() if n.type != "decision"}
            done = set(state.done_node_ids)
            missing = required - done
            if missing:
                failures.append(f"nodes not done: {', '.join(sorted(missing))}")

        if fp.require_verification_pass:
            v = state.verification or {}
            if not v.get("ok", False) and v.get("last_status") != "pending":
                failures.append("last verification did not pass")

        if fp.require_clean_or_committed_repo:
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True, text=True, timeout=10,
                    cwd=cwd,
                )
                if result.stdout.strip():
                    failures.append("repo has uncommitted changes")
            except (subprocess.SubprocessError, FileNotFoundError):
                failures.append("could not check git status")

        return {
            "ok": len(failures) == 0,
            "reason": "; ".join(failures) if failures else "all finish conditions met",
            "failures": failures,
        }
