"""Shared post-processing for ``ExplainerClient.request_clarification`` results.

Used by both the local async path (``supervisor.operator.actions``) and
the daemon path (``supervisor.daemon.server``). Keeping the escalation
rule + event payloads here ensures both code paths stay on the same
wire contract.
"""
from __future__ import annotations

from typing import Any, Callable

from supervisor.operator.models import coerce_confidence

EventWriter = Callable[[str, dict[str, Any]], None]


def finalize_clarification(
    result: dict[str, Any],
    *,
    question: str,
    escalation_threshold: float,
    write_event: EventWriter,
) -> dict[str, Any]:
    """Annotate *result* with ``escalation_recommended`` and emit timeline events.

    Emits in order:
      - ``explainer_answer`` — source-tagged answer, for channel adapters.
      - ``clarification_response`` — legacy event, preserved for back-compat.
      - ``clarification_escalation_recommended`` — only when escalation fires.

    Mutates *result* in place and returns it.
    """
    confidence = result.get("confidence")
    conf_value = coerce_confidence(confidence)
    escalate = conf_value is not None and conf_value < escalation_threshold
    result["escalation_recommended"] = escalate

    answer = result.get("answer", "")

    write_event("explainer_answer", {
        "source": "explainer",
        "question": question,
        "answer": answer,
        "confidence": confidence,
    })
    write_event("clarification_response", {
        "question": question,
        "answer": answer,
        "confidence": confidence,
        "source": "explainer",
        "escalation_recommended": escalate,
    })
    if escalate:
        write_event("clarification_escalation_recommended", {
            "question": question,
            "confidence": confidence,
            "threshold": escalation_threshold,
        })
    return result
