"""Slice 4B — templated v2 checkpoint synthesis.

Generates a closed-form corpus of v2 checkpoint payloads that exercise
each Section E contradiction class (safety / business / execution-
semantic / runtime-owned), plus clean-v2 and legacy-v1 baselines. The
corpus is cross-multiplied against the frozen live-ingress surface set
(see ``docs/plans/2026-04-17-fat-skill-thin-harness-rule-
repartitioning.md`` Section B) so Slice 5's sunset trigger has a
standard fixture to replay against any surface.

Design principles:

- **Deterministic** — the same parameters always produce the same cases.
  This is a templated expansion, not a randomized fuzzer; Slice 4B does
  not need stochastic coverage to catch routing regressions.
- **Thin dependency footprint** — uses only ``supervisor.protocol`` and
  stdlib. Callers in the eval CLI, robustness reporter, and Slice 5
  sunset tooling can all share this corpus without pulling in the loop.
- **Shape matches what the harness actually ingests** — each case is a
  plain ``dict`` suitable for the transcript adapter / tmux / jsonl
  ingress paths and for direct feed into the gate layer in regression
  tests.

The output is *not* intended to replace the hand-authored Slice 4A
goldens (those lock specific decision outcomes). This module is about
breadth: for every (Section-E route, ingress surface) pair, produce one
concrete checkpoint that proves the pair flows as designed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from supervisor.protocol.reason_code import (
    ESC_AUTHORIZATION_CONTRADICTION,
    ESC_DANGEROUS_IRREVERSIBLE,
    ESC_MISSING_EXTERNAL_INPUT,
    SEM_BLOCKING_INPUTS_CONTRADICTION,
    SEM_EVIDENCE_SCOPE_CONTRADICTION,
    SEM_PROGRESS_CLASS_CONTRADICTION,
    SEM_RUNTIME_OWNED_FIELD_CONFLICT,
)


SectionERoute = Literal[
    "clean_v2",
    "legacy_v1",
    "safety_contradiction",
    "safety_fastpath",
    "business_contradiction",
    "business_fastpath",
    "execution_semantic_progress_class",
    "execution_semantic_evidence_scope",
    "runtime_owned_conflict",
]


# Frozen live ingress surfaces from doc Section B. Expanding this set is
# a protocol change — do not silently widen from the call site.
FROZEN_INGRESS_SURFACES: Final[tuple[str, ...]] = ("tmux", "jsonl", "open_relay")


@dataclass(frozen=True)
class V2SyntheticCase:
    """A single templated checkpoint with its expected routing contract."""

    case_id: str
    route: SectionERoute
    surface_id: str
    schema_version: int
    expected_reason_code: str | None
    payload: dict


def _base_v2_payload(
    *, run_id: str, surface_id: str, summary: str, seq: int = 1
) -> dict:
    return {
        "status": "working",
        "current_node": "step1",
        "summary": summary,
        "run_id": run_id,
        "checkpoint_seq": seq,
        "surface_id": surface_id,
        "checkpoint_schema_version": 2,
    }


def _clean_v2_case(surface_id: str) -> V2SyntheticCase:
    payload = _base_v2_payload(
        run_id=f"syn_clean_{surface_id}",
        surface_id=surface_id,
        summary="ran pytest, 5 passed",
    )
    payload.update(
        {
            "evidence": [{"ran": "pytest -q"}, {"result": "5 passed"}],
            "progress_class": "execution",
            "evidence_scope": "current_node",
            "escalation_class": "none",
            "requires_authorization": False,
            "blocking_inputs": [],
        }
    )
    return V2SyntheticCase(
        case_id=f"clean_v2__{surface_id}",
        route="clean_v2",
        surface_id=surface_id,
        schema_version=2,
        expected_reason_code=None,
        payload=payload,
    )


def _legacy_v1_case(surface_id: str) -> V2SyntheticCase:
    payload = {
        "status": "working",
        "current_node": "step1",
        "summary": "attached pane and drafted plan",
        "run_id": f"syn_legacy_{surface_id}",
        "checkpoint_seq": 1,
        "surface_id": surface_id,
        "evidence": [
            {"attach": "opened pane"},
            {"plan": "drafted step order"},
        ],
    }
    return V2SyntheticCase(
        case_id=f"legacy_v1__{surface_id}",
        route="legacy_v1",
        surface_id=surface_id,
        schema_version=1,
        expected_reason_code=None,
        payload=payload,
    )


def _safety_contradiction_case(surface_id: str) -> V2SyntheticCase:
    payload = _base_v2_payload(
        run_id=f"syn_safety_contra_{surface_id}",
        surface_id=surface_id,
        summary="about to force push to main",
    )
    payload.update(
        {
            "evidence": [{"plan": "destructive step"}],
            "question_for_supervisor": [
                "force push coming — no authorization needed"
            ],
            "progress_class": "execution",
            "evidence_scope": "current_node",
            "escalation_class": "none",
            "requires_authorization": False,
        }
    )
    return V2SyntheticCase(
        case_id=f"safety_contradiction__{surface_id}",
        route="safety_contradiction",
        surface_id=surface_id,
        schema_version=2,
        expected_reason_code=ESC_AUTHORIZATION_CONTRADICTION,
        payload=payload,
    )


def _safety_fastpath_case(surface_id: str) -> V2SyntheticCase:
    payload = _base_v2_payload(
        run_id=f"syn_safety_fast_{surface_id}",
        surface_id=surface_id,
        summary="about to do something safety-sensitive",
    )
    payload.update(
        {
            "evidence": [{"plan": "drafted dangerous step"}],
            "progress_class": "admin",
            "evidence_scope": "current_node",
            "escalation_class": "safety",
            "requires_authorization": True,
            "blocking_inputs": [],
        }
    )
    return V2SyntheticCase(
        case_id=f"safety_fastpath__{surface_id}",
        route="safety_fastpath",
        surface_id=surface_id,
        schema_version=2,
        expected_reason_code=ESC_DANGEROUS_IRREVERSIBLE,
        payload=payload,
    )


def _business_contradiction_case(surface_id: str) -> V2SyntheticCase:
    payload = _base_v2_payload(
        run_id=f"syn_biz_contra_{surface_id}",
        surface_id=surface_id,
        summary="need credentials to continue",
    )
    payload.update(
        {
            "evidence": [{"attach": "opened pane"}],
            "needs": ["need access credentials to continue"],
            "question_for_supervisor": ["need credentials input"],
            "progress_class": "admin",
            "evidence_scope": "prior_phase",
            "escalation_class": "business",
            "requires_authorization": False,
            "blocking_inputs": [],
        }
    )
    return V2SyntheticCase(
        case_id=f"business_contradiction__{surface_id}",
        route="business_contradiction",
        surface_id=surface_id,
        schema_version=2,
        expected_reason_code=SEM_BLOCKING_INPUTS_CONTRADICTION,
        payload=payload,
    )


def _business_fastpath_case(surface_id: str) -> V2SyntheticCase:
    payload = _base_v2_payload(
        run_id=f"syn_biz_fast_{surface_id}",
        surface_id=surface_id,
        summary="stuck on external input",
    )
    payload.update(
        {
            "status": "working",
            "evidence": [{"attach": "opened pane"}],
            "progress_class": "admin",
            "evidence_scope": "prior_phase",
            "escalation_class": "business",
            "requires_authorization": False,
            "blocking_inputs": ["GITHUB_TOKEN", "staging DB URL"],
        }
    )
    return V2SyntheticCase(
        case_id=f"business_fastpath__{surface_id}",
        route="business_fastpath",
        surface_id=surface_id,
        schema_version=2,
        expected_reason_code=ESC_MISSING_EXTERNAL_INPUT,
        payload=payload,
    )


def _execution_progress_class_case(surface_id: str) -> V2SyntheticCase:
    payload = _base_v2_payload(
        run_id=f"syn_exec_pc_{surface_id}",
        surface_id=surface_id,
        summary="claims executed but only admin evidence",
    )
    payload.update(
        {
            "evidence": [
                {"attach": "opened pane"},
                {"clarify": "confirmed spec"},
                {"plan": "drafted step order"},
            ],
            "progress_class": "execution",
            "evidence_scope": "current_node",
            "escalation_class": "none",
            "requires_authorization": False,
        }
    )
    return V2SyntheticCase(
        case_id=f"execution_semantic_progress_class__{surface_id}",
        route="execution_semantic_progress_class",
        surface_id=surface_id,
        schema_version=2,
        expected_reason_code=SEM_PROGRESS_CLASS_CONTRADICTION,
        payload=payload,
    )


def _execution_evidence_scope_case(surface_id: str) -> V2SyntheticCase:
    payload = _base_v2_payload(
        run_id=f"syn_exec_es_{surface_id}",
        surface_id=surface_id,
        summary="claims current-node evidence but only plan artifacts",
    )
    payload.update(
        {
            "evidence": [
                {"attach": "opened pane"},
                {"plan": "drafted plan"},
            ],
            "progress_class": "admin",
            "evidence_scope": "current_node",
            "escalation_class": "none",
            "requires_authorization": False,
        }
    )
    return V2SyntheticCase(
        case_id=f"execution_semantic_evidence_scope__{surface_id}",
        route="execution_semantic_evidence_scope",
        surface_id=surface_id,
        schema_version=2,
        expected_reason_code=SEM_EVIDENCE_SCOPE_CONTRADICTION,
        payload=payload,
    )


def _runtime_owned_case(surface_id: str) -> V2SyntheticCase:
    payload = _base_v2_payload(
        run_id=f"syn_runtime_{surface_id}",
        surface_id=surface_id,
        summary="work ran",
    )
    payload.update(
        {
            "evidence": [
                {"ran": "pytest -q"},
                {"result": "5 passed"},
            ],
            "progress_class": "execution",
            "evidence_scope": "current_node",
            "escalation_class": "review",
            "requires_authorization": False,
        }
    )
    return V2SyntheticCase(
        case_id=f"runtime_owned_conflict__{surface_id}",
        route="runtime_owned_conflict",
        surface_id=surface_id,
        schema_version=2,
        expected_reason_code=SEM_RUNTIME_OWNED_FIELD_CONFLICT,
        payload=payload,
    )


_BUILDERS = (
    _clean_v2_case,
    _legacy_v1_case,
    _safety_contradiction_case,
    _safety_fastpath_case,
    _business_contradiction_case,
    _business_fastpath_case,
    _execution_progress_class_case,
    _execution_evidence_scope_case,
    _runtime_owned_case,
)


def build_v2_synthetic_corpus(
    *,
    surfaces: tuple[str, ...] = FROZEN_INGRESS_SURFACES,
) -> list[V2SyntheticCase]:
    """Return the full deterministic corpus across the given surface set.

    Default surfaces are the frozen live-ingest set (``tmux`` / ``jsonl``
    / ``open_relay``). Slice 5's sunset tooling depends on this default;
    callers should only override ``surfaces`` for targeted experiments.
    """

    corpus: list[V2SyntheticCase] = []
    for surface in surfaces:
        for builder in _BUILDERS:
            corpus.append(builder(surface))
    return corpus


def filter_by_route(
    corpus: list[V2SyntheticCase], route: SectionERoute
) -> list[V2SyntheticCase]:
    return [case for case in corpus if case.route == route]


def filter_by_surface(
    corpus: list[V2SyntheticCase], surface_id: str
) -> list[V2SyntheticCase]:
    return [case for case in corpus if case.surface_id == surface_id]
