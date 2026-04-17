"""Slice 4B — tests for robustness reporting + fallback-rate trend."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from supervisor.eval.robustness import (
    CheckpointObservation,
    compute_fallback_rate_trend,
    compute_robustness_report,
    evaluate_sunset_trigger,
)
from supervisor.eval.v2_synthetic import FROZEN_INGRESS_SURFACES
from supervisor.protocol.normalizer import normalize_checkpoint


def _cp(surface: str, *, schema: int, seq: int = 1) -> dict:
    payload = {
        "status": "working",
        "current_node": "step1",
        "summary": "test",
        "run_id": f"run_{surface}_{seq}",
        "checkpoint_seq": seq,
        "surface_id": surface,
    }
    if schema == 2:
        payload.update(
            {
                "checkpoint_schema_version": 2,
                "evidence": [{"ran": "pytest"}, {"result": "passed"}],
                "progress_class": "execution",
                "evidence_scope": "current_node",
                "escalation_class": "none",
                "requires_authorization": False,
                "blocking_inputs": [],
            }
        )
    else:
        payload["evidence"] = [{"attach": "opened pane"}]
    return payload


def _obs(surface: str, *, schema: int, at: datetime, seq: int = 1) -> CheckpointObservation:
    normalized = normalize_checkpoint(_cp(surface, schema=schema, seq=seq))
    assert normalized is not None
    return CheckpointObservation(checkpoint=normalized, observed_at=at)


def test_report_counts_v1_and_v2_per_surface():
    now = datetime(2026, 4, 17, tzinfo=timezone.utc)
    obs = [
        _obs("tmux", schema=2, at=now, seq=1),
        _obs("tmux", schema=2, at=now, seq=2),
        _obs("tmux", schema=1, at=now, seq=3),
        _obs("jsonl", schema=2, at=now, seq=1),
        _obs("open_relay", schema=1, at=now, seq=1),
    ]
    report = compute_robustness_report(obs)
    assert report.total == 5
    assert report.v2_count == 3
    assert report.v1_count == 2
    assert abs(report.fallback_rate - 0.4) < 1e-9
    assert abs(report.structured_rate - 0.6) < 1e-9

    by_surface = {s.surface_id: s for s in report.per_surface}
    assert by_surface["tmux"].v2_count == 2
    assert by_surface["tmux"].v1_count == 1
    assert by_surface["jsonl"].v2_count == 1
    assert by_surface["open_relay"].v2_count == 0


def test_report_flags_frozen_surfaces_missing_v2():
    now = datetime(2026, 4, 17, tzinfo=timezone.utc)
    obs = [
        _obs("tmux", schema=2, at=now),
        _obs("jsonl", schema=1, at=now),
    ]
    report = compute_robustness_report(obs)
    assert "tmux" in report.frozen_surfaces_with_v2
    assert "jsonl" in report.frozen_surfaces_missing_v2
    assert "open_relay" in report.frozen_surfaces_missing_v2


def test_empty_stream_produces_zero_rates():
    report = compute_robustness_report([])
    assert report.total == 0
    assert report.fallback_rate == 0.0
    assert report.structured_rate == 0.0
    assert report.frozen_surfaces_missing_v2 == frozenset(FROZEN_INGRESS_SURFACES)


def test_fallback_rate_trend_produces_one_bucket_per_day():
    ref = date(2026, 4, 17)
    base = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
    obs = [
        _obs("tmux", schema=2, at=base - timedelta(days=1)),
        _obs("tmux", schema=1, at=base - timedelta(days=1)),
        _obs("tmux", schema=2, at=base),
    ]
    trend = compute_fallback_rate_trend(obs, window_days=3, reference_day=ref)
    assert [d.day for d in trend] == [
        ref - timedelta(days=2),
        ref - timedelta(days=1),
        ref,
    ]
    buckets = {d.day: d for d in trend}
    assert buckets[ref - timedelta(days=2)].total == 0
    assert buckets[ref - timedelta(days=1)].total == 2
    assert abs(buckets[ref - timedelta(days=1)].fallback_rate - 0.5) < 1e-9
    assert buckets[ref].total == 1
    assert buckets[ref].fallback_rate == 0.0


def test_sunset_trigger_fires_when_both_conditions_hold():
    ref = date(2026, 4, 17)
    base = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
    obs = []
    for offset in range(14):
        for surface in FROZEN_INGRESS_SURFACES:
            obs.append(
                _obs(surface, schema=2, at=base - timedelta(days=offset), seq=offset + 1)
            )

    status = evaluate_sunset_trigger(obs, reference_day=ref)
    assert status.fallback_rate_below_threshold
    assert status.consecutive_days_below == 14
    assert status.frozen_surfaces_covered
    assert status.ready_to_sunset


def test_sunset_trigger_blocks_when_surface_missing_v2():
    ref = date(2026, 4, 17)
    base = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
    obs = []
    for offset in range(14):
        obs.append(_obs("tmux", schema=2, at=base - timedelta(days=offset), seq=offset))
        obs.append(_obs("jsonl", schema=2, at=base - timedelta(days=offset), seq=offset))
        # open_relay stays on v1 — trigger must not fire.
        obs.append(_obs("open_relay", schema=1, at=base - timedelta(days=offset), seq=offset))

    status = evaluate_sunset_trigger(obs, reference_day=ref)
    assert not status.frozen_surfaces_covered
    assert "open_relay" in status.frozen_surfaces_missing_v2
    assert not status.ready_to_sunset


def test_sunset_trigger_blocks_on_quiet_day():
    ref = date(2026, 4, 17)
    base = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
    obs = []
    # Every day at v2 except one in the middle with zero observations.
    for offset in range(14):
        if offset == 7:
            continue
        for surface in FROZEN_INGRESS_SURFACES:
            obs.append(
                _obs(surface, schema=2, at=base - timedelta(days=offset), seq=offset)
            )

    status = evaluate_sunset_trigger(obs, reference_day=ref)
    assert status.consecutive_days_below < 14
    assert not status.ready_to_sunset


def test_sunset_trigger_ignores_pre_window_v2_observations():
    # Per Section B of the repartitioning doc, old pre-window v2
    # observations do not count toward the coverage trigger. Here
    # open_relay emitted a v2 checkpoint 30 days ago but only v1 inside
    # the window — the trigger must NOT fire.
    ref = date(2026, 4, 17)
    base = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
    obs = []
    for offset in range(14):
        obs.append(_obs("tmux", schema=2, at=base - timedelta(days=offset), seq=offset))
        obs.append(_obs("jsonl", schema=2, at=base - timedelta(days=offset), seq=offset))
        obs.append(
            _obs("open_relay", schema=1, at=base - timedelta(days=offset), seq=offset)
        )
    # Stale v2 observation from 30 days ago — before the window starts.
    obs.append(_obs("open_relay", schema=2, at=base - timedelta(days=30), seq=99))

    status = evaluate_sunset_trigger(obs, reference_day=ref)
    assert not status.frozen_surfaces_covered
    assert "open_relay" in status.frozen_surfaces_missing_v2
    assert not status.ready_to_sunset


def test_sunset_trigger_blocks_when_fallback_rate_too_high():
    ref = date(2026, 4, 17)
    base = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
    obs = []
    for offset in range(14):
        # 50/50 v1/v2 — fallback rate well above the 5% threshold.
        for surface in FROZEN_INGRESS_SURFACES:
            obs.append(
                _obs(surface, schema=2, at=base - timedelta(days=offset), seq=offset * 2)
            )
            obs.append(
                _obs(surface, schema=1, at=base - timedelta(days=offset), seq=offset * 2 + 1)
            )

    status = evaluate_sunset_trigger(obs, reference_day=ref)
    assert not status.fallback_rate_below_threshold
    assert not status.ready_to_sunset
