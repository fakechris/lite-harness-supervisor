"""Slice 4B — robustness reporting + fallback-rate trend analysis.

The robustness report is the quantitative companion to the Slice 4A
invariant oracles. Where Slice 4A asks "did this specific golden route
correctly?", this module asks "across a stream of observed checkpoints,
what fraction still needed the legacy heuristic fallback, and did that
fraction drop over time?"

Output from this module feeds Slice 5's v1 live-path sunset trigger
(see ``docs/plans/2026-04-17-fat-skill-thin-harness-rule-
repartitioning.md`` Section B). Per the plan, the sunset requires BOTH:

1. heuristic fallback-rate below threshold for 14 consecutive days
2. every surface in the frozen live ingress set has produced at least
   one successful ``checkpoint_schema_version=2`` live checkpoint

This module computes both signals. Deciding whether to flip deprecation
→ enforcement is Slice 5's job; we only supply the data.

Design notes:

- Works off ``NormalizedCheckpoint`` so the report is consistent with
  what the gate layer actually sees. No parallel interpretation.
- Pure functions over an in-memory iterable. Persistence format is
  left to the caller — Slice 5 decides whether to log, rollup, or
  snapshot.
- Timestamps are supplied by the caller, not inferred from the
  checkpoint. ``checkpoint_seq`` is monotone per run, not wall-clock.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from supervisor.eval.v2_synthetic import FROZEN_INGRESS_SURFACES
from supervisor.protocol.normalizer import (
    STRUCTURED_SCHEMA_VERSION,
    NormalizedCheckpoint,
)


@dataclass(frozen=True)
class CheckpointObservation:
    """A single point in the observation stream used by the report.

    ``observed_at`` is a UTC timestamp supplied by the ingress adapter at
    the time the checkpoint was accepted. We deliberately do not read it
    off the checkpoint itself — the worker cannot self-report a wall
    clock the runtime trusts.
    """

    checkpoint: NormalizedCheckpoint
    observed_at: datetime


@dataclass(frozen=True)
class SurfaceSummary:
    surface_id: str
    total: int
    v2_count: int
    v1_count: int

    @property
    def v2_adoption_rate(self) -> float:
        return (self.v2_count / self.total) if self.total else 0.0

    @property
    def has_any_v2(self) -> bool:
        return self.v2_count > 0


@dataclass(frozen=True)
class RobustnessReport:
    """Point-in-time robustness summary across an observation stream.

    Field semantics:

    - ``fallback_rate``: fraction of observations that used the legacy
      (v1) heuristic path. A high fallback rate means the thin-harness
      work has not yet displaced the old classifier; Slice 5's sunset
      cannot fire.
    - ``structured_rate``: complement of fallback_rate — fraction that
      hit the structured v2 path.
    - ``per_surface``: breakdown keyed by ``surface_id`` so the sunset
      trigger can enforce "every frozen surface observed at v2".
    - ``frozen_surfaces_with_v2``: subset of the frozen live ingress
      set with at least one v2 observation during the window. Slice 5
      compares this against ``FROZEN_INGRESS_SURFACES`` for trigger #2.
    """

    total: int
    v2_count: int
    v1_count: int
    fallback_rate: float
    structured_rate: float
    per_surface: tuple[SurfaceSummary, ...]
    frozen_surfaces_with_v2: frozenset[str]
    frozen_surfaces_missing_v2: frozenset[str]


@dataclass(frozen=True)
class DailyFallbackRate:
    day: date
    total: int
    v1_count: int
    fallback_rate: float


@dataclass(frozen=True)
class SunsetTriggerStatus:
    """Slice 5 reads this to decide deprecation → enforcement.

    Both signals must be ``True`` for the sunset to fire. The helper
    only reports status; the actual flip is policy that lives outside
    this module.
    """

    fallback_rate_below_threshold: bool
    consecutive_days_below: int
    required_consecutive_days: int
    frozen_surfaces_covered: bool
    frozen_surfaces_missing_v2: frozenset[str]
    threshold: float

    @property
    def ready_to_sunset(self) -> bool:
        return self.fallback_rate_below_threshold and self.frozen_surfaces_covered


def compute_robustness_report(
    observations: Iterable[CheckpointObservation],
    *,
    frozen_surfaces: tuple[str, ...] = FROZEN_INGRESS_SURFACES,
) -> RobustnessReport:
    """Aggregate an observation stream into a single robustness snapshot."""

    totals: dict[str, int] = defaultdict(int)
    v2_totals: dict[str, int] = defaultdict(int)
    v1_totals: dict[str, int] = defaultdict(int)

    total = 0
    v2_count = 0
    v1_count = 0

    for observation in observations:
        cp = observation.checkpoint
        surface_id = cp.surface_id or "unknown"
        totals[surface_id] += 1
        total += 1
        if cp.schema_version == STRUCTURED_SCHEMA_VERSION:
            v2_totals[surface_id] += 1
            v2_count += 1
        else:
            v1_totals[surface_id] += 1
            v1_count += 1

    per_surface = tuple(
        SurfaceSummary(
            surface_id=surface,
            total=totals[surface],
            v2_count=v2_totals[surface],
            v1_count=v1_totals[surface],
        )
        for surface in sorted(totals)
    )

    frozen_set = frozenset(frozen_surfaces)
    covered = frozenset(
        surface for surface, count in v2_totals.items() if count > 0
    )
    frozen_covered = frozen_set & covered
    frozen_missing = frozen_set - covered

    fallback_rate = (v1_count / total) if total else 0.0
    structured_rate = (v2_count / total) if total else 0.0

    return RobustnessReport(
        total=total,
        v2_count=v2_count,
        v1_count=v1_count,
        fallback_rate=fallback_rate,
        structured_rate=structured_rate,
        per_surface=per_surface,
        frozen_surfaces_with_v2=frozen_covered,
        frozen_surfaces_missing_v2=frozen_missing,
    )


def compute_fallback_rate_trend(
    observations: Iterable[CheckpointObservation],
    *,
    window_days: int = 14,
    reference_day: date | None = None,
) -> list[DailyFallbackRate]:
    """Return per-day fallback rates across the last ``window_days`` days.

    Days with zero observations are included with ``total=0`` and a
    ``fallback_rate=0.0`` — callers that need to distinguish "no data"
    from "no fallback" should check ``total``. The sunset helper below
    treats a zero-observation day as *not* satisfying the threshold so
    a quiet day cannot silently advance the streak.
    """

    if reference_day is None:
        reference_day = datetime.now(tz=timezone.utc).date()

    bucket_totals: dict[date, int] = defaultdict(int)
    bucket_v1: dict[date, int] = defaultdict(int)

    observation_list = list(observations)
    for observation in observation_list:
        ts = observation.observed_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        day = ts.astimezone(timezone.utc).date()
        bucket_totals[day] += 1
        if observation.checkpoint.schema_version != STRUCTURED_SCHEMA_VERSION:
            bucket_v1[day] += 1

    trend: list[DailyFallbackRate] = []
    for offset in range(window_days - 1, -1, -1):
        day = reference_day - timedelta(days=offset)
        total = bucket_totals.get(day, 0)
        v1_count = bucket_v1.get(day, 0)
        rate = (v1_count / total) if total else 0.0
        trend.append(
            DailyFallbackRate(
                day=day, total=total, v1_count=v1_count, fallback_rate=rate
            )
        )
    return trend


def evaluate_sunset_trigger(
    observations: Iterable[CheckpointObservation],
    *,
    threshold: float = 0.05,
    required_consecutive_days: int = 14,
    reference_day: date | None = None,
    frozen_surfaces: tuple[str, ...] = FROZEN_INGRESS_SURFACES,
) -> SunsetTriggerStatus:
    """Return whether both Slice-5 sunset conditions are satisfied.

    The threshold defaults to 5% fallback rate; the required streak is
    14 consecutive days per the plan's Section B. A day with zero
    observations does **not** count as "below threshold" — otherwise a
    silent surface would drift the streak forward without evidence.
    """

    observation_list = list(observations)

    if reference_day is None:
        reference_day = datetime.now(tz=timezone.utc).date()
    window_start = reference_day - timedelta(days=required_consecutive_days - 1)

    # Per Section B of the repartitioning doc, "old pre-window
    # observations do not count toward the trigger" — including for the
    # surface-coverage check. A v2 emission 30 days ago does NOT satisfy
    # the trigger if the surface has only emitted v1 inside the window.
    windowed = [
        obs
        for obs in observation_list
        if obs.observed_at.astimezone(timezone.utc).date() >= window_start
    ]

    trend = compute_fallback_rate_trend(
        windowed,
        window_days=required_consecutive_days,
        reference_day=reference_day,
    )

    consecutive = 0
    for daily in trend:
        if daily.total > 0 and daily.fallback_rate <= threshold:
            consecutive += 1
        else:
            consecutive = 0

    fallback_ok = consecutive >= required_consecutive_days

    report = compute_robustness_report(windowed, frozen_surfaces=frozen_surfaces)
    surfaces_ok = not report.frozen_surfaces_missing_v2

    return SunsetTriggerStatus(
        fallback_rate_below_threshold=fallback_ok,
        consecutive_days_below=consecutive,
        required_consecutive_days=required_consecutive_days,
        frozen_surfaces_covered=surfaces_ok,
        frozen_surfaces_missing_v2=report.frozen_surfaces_missing_v2,
        threshold=threshold,
    )
