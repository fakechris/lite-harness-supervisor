"""Tests for supervisor.operator.models — frozen projections.

Covers the new DriftAssessment + ExchangeView dataclasses added in 0.3.6.
RunSnapshot / RunTimelineEvent / RunEventPlaneSummary are exercised by
test_operator_api.py.
"""
from __future__ import annotations

import pytest

from supervisor.operator.models import DriftAssessment, ExchangeView


class TestDriftAssessment:
    def test_from_dict_wraps_explainer_output(self):
        raw = {
            "status": "drifting",
            "reasons": ["retry budget near cap", "node mismatch"],
            "evidence": ["retries_used=4"],
            "codebase_signals": ["supervisor/loop.py touched"],
            "recommended_action": "Pause and review",
            "confidence": 0.72,
        }
        d = DriftAssessment.from_dict(raw, run_id="run-1")
        assert d.run_id == "run-1"
        assert d.status == "drifting"
        assert d.reasons == ["retry budget near cap", "node mismatch"]
        assert d.evidence == ["retries_used=4"]
        assert d.codebase_signals == ["supervisor/loop.py touched"]
        assert d.recommended_action == "Pause and review"
        assert d.confidence == pytest.approx(0.72)

    def test_unknown_status_falls_back_to_watch(self):
        d = DriftAssessment.from_dict({"status": "wild_guess"})
        assert d.status == "watch"

    def test_missing_lists_default_to_empty(self):
        d = DriftAssessment.from_dict({"status": "on_track"})
        assert d.reasons == []
        assert d.evidence == []
        assert d.codebase_signals == []
        assert d.recommended_action == ""
        assert d.confidence is None

    def test_recommended_action_plan_alias(self):
        d = DriftAssessment.from_dict(
            {"status": "watch", "recommended_operator_action": "monitor"},
        )
        assert d.recommended_action == "monitor"

    def test_non_numeric_confidence_coerced_to_none(self):
        d = DriftAssessment.from_dict({"status": "watch", "confidence": "maybe"})
        assert d.confidence is None

    def test_to_dict_roundtrip(self):
        raw = {
            "status": "on_track",
            "reasons": ["r1"],
            "evidence": [],
            "codebase_signals": [],
            "recommended_action": "none",
            "confidence": 0.9,
        }
        d = DriftAssessment.from_dict(raw, run_id="r")
        out = d.to_dict()
        assert out["run_id"] == "r"
        assert out["status"] == "on_track"
        assert out["confidence"] == 0.9
        # Lists are copied, not shared — operator consumers can mutate safely.
        out["reasons"].append("mutated")
        assert d.reasons == ["r1"]

    def test_frozen(self):
        d = DriftAssessment.from_dict({"status": "watch"})
        with pytest.raises(Exception):
            d.status = "on_track"  # type: ignore[misc]


class TestExchangeView:
    def test_from_legacy_recent_exchange_shape(self):
        """api.recent_exchange() returns a dict with last_*_summary keys."""
        raw = {
            "last_checkpoint_summary": "worker finished step A",
            "last_instruction_summary": "proceed to step B",
        }
        ex = ExchangeView.from_dict(raw, run_id="run-x")
        assert ex.run_id == "run-x"
        assert ex.worker_text_excerpt == "worker finished step A"
        assert ex.supervisor_instruction_excerpt == "proceed to step B"
        assert ex.explanation_zh == ""
        assert ex.explanation_en == ""
        assert ex.confidence is None

    def test_from_explainer_shape_preserves_explanations(self):
        raw = {
            "run_id": "run-y",
            "window_start": "2026-04-22T10:00:00Z",
            "window_end": "2026-04-22T10:05:00Z",
            "worker_text_excerpt": "reading file",
            "supervisor_instruction_excerpt": "keep going",
            "checkpoint_excerpt": "<checkpoint>...</checkpoint>",
            "explanation_zh": "worker 正在读取文件",
            "explanation_en": "worker is reading the file",
            "confidence": 0.82,
        }
        ex = ExchangeView.from_dict(raw)
        assert ex.run_id == "run-y"
        assert ex.explanation_zh == "worker 正在读取文件"
        assert ex.explanation_en == "worker is reading the file"
        assert ex.confidence == pytest.approx(0.82)

    def test_empty_input_yields_empty_view(self):
        ex = ExchangeView.from_dict({})
        assert ex.run_id == ""
        assert ex.worker_text_excerpt == ""
        assert ex.confidence is None

    def test_non_numeric_confidence_coerced_to_none(self):
        ex = ExchangeView.from_dict({"confidence": "n/a"})
        assert ex.confidence is None

    def test_to_dict_roundtrip(self):
        raw = {"last_checkpoint_summary": "x", "last_instruction_summary": "y"}
        ex = ExchangeView.from_dict(raw, run_id="r")
        out = ex.to_dict()
        assert out["worker_text_excerpt"] == "x"
        assert out["supervisor_instruction_excerpt"] == "y"

    def test_frozen(self):
        ex = ExchangeView.from_dict({})
        with pytest.raises(Exception):
            ex.run_id = "tampered"  # type: ignore[misc]
