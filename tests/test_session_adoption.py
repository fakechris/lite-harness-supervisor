"""Tests for session adoption / inheritance at run registration (Task 1b).

Covers the session-first correlation rule: a new run adopts an existing
active session on (workspace_root, spec_id) when one applies, else creates
a fresh session. Resumed runs preserve their session_id. A session survives
after all its runs terminate.
"""
from __future__ import annotations

import os

import pytest

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore


@pytest.fixture
def spec():
    return load_spec("specs/examples/linear_plan.example.yaml")


def test_new_run_creates_session_when_none_exists(tmp_path, spec):
    runtime_root = tmp_path / "runtime"
    run_dir = runtime_root / "runs" / "run_1"
    run_dir.mkdir(parents=True)
    store = StateStore(str(run_dir), runtime_root=str(runtime_root))

    state = store.load_or_init(
        spec,
        spec_path="specs/examples/linear_plan.example.yaml",
        workspace_root=str(tmp_path),
    )

    assert state.session_id
    assert state.session_id.startswith("session_")

    # The session is durably recorded in the shared store.
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == state.session_id
    assert sessions[0].workspace_root == str(tmp_path)
    assert sessions[0].spec_id == state.spec_id
    assert sessions[0].status == "active"


def test_second_run_adopts_existing_session(tmp_path, spec):
    """Same workspace + spec + open session ⇒ adoption, not a new session."""
    runtime_root = tmp_path / "runtime"

    run_a_dir = runtime_root / "runs" / "run_a"
    run_a_dir.mkdir(parents=True)
    store_a = StateStore(str(run_a_dir), runtime_root=str(runtime_root))
    state_a = store_a.load_or_init(
        spec,
        spec_path="specs/examples/linear_plan.example.yaml",
        workspace_root=str(tmp_path),
    )

    run_b_dir = runtime_root / "runs" / "run_b"
    run_b_dir.mkdir(parents=True)
    store_b = StateStore(str(run_b_dir), runtime_root=str(runtime_root))
    state_b = store_b.load_or_init(
        spec,
        spec_path="specs/examples/linear_plan.example.yaml",
        workspace_root=str(tmp_path),
    )

    assert state_a.session_id == state_b.session_id
    assert state_a.run_id != state_b.run_id
    # Still exactly one session.
    sessions = store_b.list_sessions()
    assert len(sessions) == 1


def test_different_workspace_gets_own_session(tmp_path, spec):
    runtime_root = tmp_path / "runtime"

    for wt_name in ("wt1", "wt2"):
        wt = tmp_path / wt_name
        wt.mkdir()
        run_dir = runtime_root / "runs" / f"run_{wt_name}"
        run_dir.mkdir(parents=True)
        store = StateStore(str(run_dir), runtime_root=str(runtime_root))
        store.load_or_init(
            spec,
            spec_path="specs/examples/linear_plan.example.yaml",
            workspace_root=str(wt),
        )

    # Distinct worktrees ⇒ distinct sessions even under same spec.
    final_store = StateStore(
        str(runtime_root / "runs" / "run_wt2"),
        runtime_root=str(runtime_root),
    )
    assert len({s.session_id for s in final_store.list_sessions()}) == 2


def test_resumed_run_preserves_session_id(tmp_path, spec):
    """Reloading a run's state.json must keep the original session_id."""
    runtime_root = tmp_path / "runtime"
    run_dir = runtime_root / "runs" / "run_keep"
    run_dir.mkdir(parents=True)
    store = StateStore(str(run_dir), runtime_root=str(runtime_root))

    state_first = store.load_or_init(
        spec,
        spec_path="specs/examples/linear_plan.example.yaml",
        workspace_root=str(tmp_path),
    )
    first_session = state_first.session_id
    assert first_session

    # Fresh StateStore on the same dir — simulates daemon restart / resume.
    store2 = StateStore(str(run_dir), runtime_root=str(runtime_root))
    state_second = store2.load_or_init(
        spec,
        spec_path="specs/examples/linear_plan.example.yaml",
        workspace_root=str(tmp_path),
    )
    assert state_second.session_id == first_session


def test_session_resolvable_with_zero_active_runs(tmp_path, spec):
    """After a run terminates, its session_id is still resolvable."""
    runtime_root = tmp_path / "runtime"
    run_dir = runtime_root / "runs" / "run_done"
    run_dir.mkdir(parents=True)
    store = StateStore(str(run_dir), runtime_root=str(runtime_root))
    state = store.load_or_init(
        spec,
        spec_path="specs/examples/linear_plan.example.yaml",
        workspace_root=str(tmp_path),
    )
    session_id = state.session_id

    # Simulate run teardown: remove state.json (run archived/completed).
    store.state_path.unlink()

    # The session is still durable and findable.
    discover = StateStore(
        str(runtime_root / "runs" / "other"),
        runtime_root=str(runtime_root),
    )
    (runtime_root / "runs" / "other").mkdir(parents=True, exist_ok=True)
    loaded = discover.load_session(session_id)
    assert loaded is not None
    assert loaded.status == "active"


def test_run_context_surfaces_session_id(tmp_path, spec):
    """RunContext should pick up session_id from state.json."""
    from supervisor.operator.run_context import RunContext

    worktree = tmp_path / "wt"
    worktree.mkdir()
    # Initial store in a temporary location so we can discover the generated
    # run_id, then re-materialise the run under the canonical RunContext
    # path layout (<worktree>/.supervisor/runtime/runs/<run_id>).
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    runtime_root = worktree / ".supervisor" / "runtime"
    seed_store = StateStore(str(seed_dir), runtime_root=str(runtime_root))
    state = seed_store.load_or_init(
        spec,
        spec_path="specs/examples/linear_plan.example.yaml",
        workspace_root=str(worktree),
    )
    run_dir = runtime_root / "runs" / state.run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(seed_store.state_path.read_text())

    run_dict = {
        "run_id": state.run_id,
        "worktree": str(worktree),
        "tag": "daemon",
        "top_state": "READY",
        "pane_target": "",
        "socket": "",
    }
    ctx = RunContext.from_run_dict(run_dict)
    assert ctx.session_id == state.session_id


def test_legacy_state_without_session_id_gets_one_on_resume(tmp_path, spec):
    """States written before Task 1a must gracefully backfill a session_id."""
    runtime_root = tmp_path / "runtime"
    run_dir = runtime_root / "runs" / "run_legacy"
    run_dir.mkdir(parents=True)
    store = StateStore(str(run_dir), runtime_root=str(runtime_root))

    # Write a legacy-shaped state.json (no session_id field).
    import json
    from supervisor.domain.enums import TopState
    legacy = {
        "run_id": "run_legacy",
        "spec_id": spec.id,
        "mode": spec.kind,
        "top_state": TopState.READY.value,
        "current_node_id": spec.first_node_id(),
        "spec_path": "specs/examples/linear_plan.example.yaml",
        "spec_hash": StateStore._hash_spec("specs/examples/linear_plan.example.yaml"),
        "workspace_root": str(tmp_path),
        "schema_version": 1,
    }
    store.state_path.write_text(json.dumps(legacy))

    state = store.load_or_init(
        spec,
        spec_path="specs/examples/linear_plan.example.yaml",
        workspace_root=str(tmp_path),
    )
    assert state.session_id, "legacy run should be backfilled with a session_id on resume"
