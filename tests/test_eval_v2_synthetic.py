"""Slice 4B — tests for v2 templated checkpoint synthesis.

Verifies that the deterministic corpus:

- cross-multiplies every Section E route with every ingress surface
- tags each case with the reason_code the routing layer should produce
- produces payloads that actually flow through the normalizer + gate
  layer to the expected decision

The last class of assertions is the important one: if the synthesis
module drifts from the actual gate routing, Slice 5's sunset tooling
would silently paper over real regressions.
"""
from __future__ import annotations

from supervisor.domain.enums import DecisionType, TopState
from supervisor.eval.v2_synthetic import (
    FROZEN_INGRESS_SURFACES,
    build_v2_synthetic_corpus,
    filter_by_route,
    filter_by_surface,
)
from supervisor.loop import SupervisorLoop
from supervisor.plan.loader import load_spec
from supervisor.protocol.normalizer import normalize_checkpoint
from supervisor.protocol.reason_code import is_valid_reason_code
from supervisor.storage.state_store import StateStore


def test_corpus_covers_every_route_on_every_frozen_surface():
    corpus = build_v2_synthetic_corpus()
    by_key = {(case.route, case.surface_id) for case in corpus}
    expected_routes = {
        "clean_v2",
        "legacy_v1",
        "safety_contradiction",
        "safety_fastpath",
        "business_contradiction",
        "business_fastpath",
        "execution_semantic_progress_class",
        "execution_semantic_evidence_scope",
        "runtime_owned_conflict",
    }
    for route in expected_routes:
        for surface in FROZEN_INGRESS_SURFACES:
            assert (route, surface) in by_key, f"missing {route=} {surface=}"


def test_corpus_payloads_normalize_at_declared_schema_version():
    corpus = build_v2_synthetic_corpus()
    for case in corpus:
        normalized = normalize_checkpoint(case.payload)
        assert normalized is not None, case.case_id
        assert normalized.schema_version == case.schema_version, case.case_id


def test_expected_reason_codes_are_all_wire_valid():
    corpus = build_v2_synthetic_corpus()
    for case in corpus:
        if case.expected_reason_code is None:
            continue
        assert is_valid_reason_code(case.expected_reason_code), case.case_id


def test_filter_helpers_return_disjoint_partitions():
    corpus = build_v2_synthetic_corpus()
    clean = filter_by_route(corpus, "clean_v2")
    assert {case.surface_id for case in clean} == set(FROZEN_INGRESS_SURFACES)

    tmux_cases = filter_by_surface(corpus, "tmux")
    assert all(case.surface_id == "tmux" for case in tmux_cases)
    assert len(tmux_cases) == len(corpus) // len(FROZEN_INGRESS_SURFACES)


def _drive(tmp_path, payload):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path))
    state = store.load_or_init(spec)
    state.top_state = TopState.ATTACHED
    state.last_agent_checkpoint = {**payload, "current_node": state.current_node_id}
    loop = SupervisorLoop(store)
    return loop.gate(spec, state)


def test_safety_routes_fail_closed_to_escalate(tmp_path):
    corpus = build_v2_synthetic_corpus()
    for case in filter_by_route(corpus, "safety_contradiction"):
        decision = _drive(tmp_path / case.case_id, case.payload)
        assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value, case.case_id
        assert decision.reason_code == case.expected_reason_code, case.case_id


def test_safety_fastpath_routes_to_escalate(tmp_path):
    corpus = build_v2_synthetic_corpus()
    for case in filter_by_route(corpus, "safety_fastpath"):
        decision = _drive(tmp_path / case.case_id, case.payload)
        assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value, case.case_id
        assert decision.reason_code == case.expected_reason_code, case.case_id


def test_business_routes_escalate(tmp_path):
    corpus = build_v2_synthetic_corpus()
    for case in filter_by_route(corpus, "business_contradiction") + filter_by_route(
        corpus, "business_fastpath"
    ):
        decision = _drive(tmp_path / case.case_id, case.payload)
        assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value, case.case_id
        assert decision.reason_code == case.expected_reason_code, case.case_id


def test_execution_semantic_routes_reinject(tmp_path):
    corpus = build_v2_synthetic_corpus()
    for case in filter_by_route(
        corpus, "execution_semantic_progress_class"
    ) + filter_by_route(corpus, "execution_semantic_evidence_scope"):
        decision = _drive(tmp_path / case.case_id, case.payload)
        assert decision.decision == DecisionType.RE_INJECT.value, case.case_id
        assert decision.reason_code == case.expected_reason_code, case.case_id


def test_runtime_owned_conflict_does_not_escalate_by_contradiction(tmp_path):
    corpus = build_v2_synthetic_corpus()
    for case in filter_by_route(corpus, "runtime_owned_conflict"):
        decision = _drive(tmp_path / case.case_id, case.payload)
        # Runtime-owned conflict is demoted to a log-only signal — the
        # decision must NOT be an escalation driven by the contradiction
        # route. CONTINUE is acceptable; any escalation with
        # sem.runtime_owned_field_conflict would indicate the demotion
        # leaked into the decision path.
        assert decision.reason_code != case.expected_reason_code, case.case_id
