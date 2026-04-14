"""Tests for the eval improve orchestration workflow."""
from __future__ import annotations

import argparse
import sys

from supervisor import app


def _make_args(**overrides):
    defaults = {
        "eval_action": "improve",
        "suite": "approval-core",
        "suite_file": None,
        "baseline_policy": "builtin-approval-v1",
        "objective": "reduce_false_approval",
        "run_id": [],
        "approved_by": "",
        "max_mismatch_rate": 0.25,
        "max_friction_events": 0,
        "dry_run": False,
        "json": False,
        "config": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_improve_dry_run_stops_after_propose(tmp_path, capsys):
    """--dry-run should propose a candidate and stop."""
    args = _make_args(dry_run=True)
    rc = app.cmd_eval(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Step 1/6" in out
    assert "Candidate:" in out
    assert "--dry-run" in out
    # Should not reach step 2
    assert "Step 2/6" not in out


def test_improve_full_loop_stops_at_promote_without_approver(tmp_path, capsys):
    """Without --approved-by, the loop should stop before promoting."""
    args = _make_args()
    rc = app.cmd_eval(args)
    assert rc == 0
    out = capsys.readouterr().out
    # Should reach gate decision
    assert "Step 4/6" in out
    # Should not promote (no --approved-by)
    assert "promote-candidate" in out or "awaiting_approval" in out or "not promoting" in out


def test_improve_full_loop_with_approval(tmp_path, capsys):
    """With --approved-by, the loop should attempt promotion."""
    args = _make_args(approved_by="human")
    rc = app.cmd_eval(args)
    out = capsys.readouterr().out
    # Should reach step 6
    assert "Step 6/6" in out
    # Either promoted or held (depends on eval results)
    assert "Promoted:" in out or "not promoting" in out
