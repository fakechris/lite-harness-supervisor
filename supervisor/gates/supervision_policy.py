"""Capability-aware supervision policy engine.

Determines how strongly the supervisor should intervene based on
worker profile, acceptance contract risk, and failure history.
"""
from __future__ import annotations

from supervisor.domain.models import WorkerProfile, AcceptanceContract, SupervisionPolicy


DEFAULT_FAILURE_THRESHOLD = 3


class SupervisionPolicyEngine:
    """Rule-based policy selection.

    Three modes:
    - strict_verifier: strong worker, standard risk. Supervisor only checks
      evidence and runs verifiers. Does not give detailed guidance.
    - collaborative_reviewer: mixed capability or moderate risk. Supervisor
      asks worker to propose approach + risks before executing.
    - directive_lead: weak worker, high risk, or repeated failures. Supervisor
      gives detailed sub-steps, one action at a time.

    Default is strict_verifier — the system trusts strong workers by default.
    """

    def determine(
        self,
        worker: WorkerProfile,
        contract: AcceptanceContract,
        state,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    ) -> SupervisionPolicy:
        risk = contract.risk_class
        trust = worker.trust_level
        failures = state.current_attempt

        # High risk or critical → at least collaborative
        if risk in ("high", "critical"):
            if trust == "low" or failures >= max(failure_threshold - 1, 1):
                return SupervisionPolicy(
                    mode="directive_lead",
                    reason=f"high risk ({risk}) + {'low trust' if trust == 'low' else f'{failures} failures'}",
                    risk_class=risk,
                    failure_threshold=failure_threshold,
                )
            return SupervisionPolicy(
                mode="collaborative_reviewer",
                reason=f"high risk ({risk})",
                risk_class=risk,
                failure_threshold=failure_threshold,
            )

        # Low trust worker → collaborative by default
        if trust == "low":
            if failures >= failure_threshold:
                return SupervisionPolicy(
                    mode="directive_lead",
                    reason=f"low trust worker + {failures} failures",
                    risk_class=risk,
                    failure_threshold=failure_threshold,
                )
            return SupervisionPolicy(
                mode="collaborative_reviewer",
                reason="low trust worker",
                risk_class=risk,
                failure_threshold=failure_threshold,
            )

        # Consecutive failures → escalate mode
        if failures >= failure_threshold:
            return SupervisionPolicy(
                mode="directive_lead",
                reason=f"{failures} consecutive failures",
                risk_class=risk,
                failure_threshold=failure_threshold,
            )

        # Default: trust the worker
        return SupervisionPolicy(
            mode="strict_verifier",
            reason="default — strong worker, standard risk",
            risk_class=risk,
            failure_threshold=failure_threshold,
        )
