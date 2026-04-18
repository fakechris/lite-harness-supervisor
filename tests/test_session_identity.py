"""Tests for Session — the cross-run logical correlation entity.

A Session is distinct from a SessionRun (which wraps a single SupervisorState).
One Session may be associated with 0..N runs across its lifetime: retries,
replans, re-executes, resumes, or pre-run (plan-phase) work where no run
exists yet. These tests pin the session entity's identity and lifetime.
"""
from __future__ import annotations

from supervisor.domain.models import Session
from supervisor.storage.state_store import StateStore


def test_session_has_its_own_id(tmp_path):
    """A session carries a stable id distinct from any run_id."""
    session = Session(workspace_root=str(tmp_path), spec_id="spec_x")
    assert session.session_id
    assert session.session_id.startswith("session_")
    # ids are unique across constructions
    other = Session(workspace_root=str(tmp_path), spec_id="spec_x")
    assert other.session_id != session.session_id


def test_session_round_trip_to_from_dict():
    original = Session(
        workspace_root="/tmp/x",
        spec_id="spec_y",
        label="plan review",
        metadata={"origin": "operator"},
    )
    restored = Session.from_dict(original.to_dict())
    assert restored.session_id == original.session_id
    assert restored.workspace_root == "/tmp/x"
    assert restored.spec_id == "spec_y"
    assert restored.label == "plan review"
    assert restored.metadata == {"origin": "operator"}
    assert restored.status == "active"


def test_session_survives_in_shared_store_across_runs(tmp_path):
    """A session persists independent of any run's lifecycle.

    Simulates: session created during run_A's registration, then run_A
    terminates (its state.json is archived), and a subsequent process
    still resolves the same session_id via the shared store.
    """
    run_a_dir = tmp_path / "runs" / "run_a"
    run_a_dir.mkdir(parents=True)
    store_a = StateStore(str(run_a_dir), runtime_root=str(tmp_path))

    session = Session(workspace_root=str(tmp_path), spec_id="spec_z", label="bundle")
    store_a.save_session(session)

    # New run, new per-run store — but same runtime root.
    run_b_dir = tmp_path / "runs" / "run_b"
    run_b_dir.mkdir(parents=True)
    store_b = StateStore(str(run_b_dir), runtime_root=str(tmp_path))

    loaded = store_b.load_session(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.spec_id == "spec_z"
    assert loaded.label == "bundle"


def test_session_survives_after_all_runs_terminate(tmp_path):
    """Closing a session is explicit; end of a run does not close it.

    This is the load-bearing property behind Rule 6 (return side must work
    even if no run is currently attached).
    """
    store = StateStore(str(tmp_path / "runtime"), runtime_root=str(tmp_path / "runtime"))
    session = Session(workspace_root=str(tmp_path), spec_id="spec_a")
    store.save_session(session)

    # Mark session unchanged; simulate run_a teardown by just not touching it.
    # Session should still be resolvable and still report status=active.
    again = store.load_session(session.session_id)
    assert again is not None
    assert again.status == "active"


def test_list_sessions_returns_latest_record_per_id(tmp_path):
    """sessions.jsonl is append-only; latest line wins for a given id.

    Required for 'open waits' queries and for close/mutation semantics.
    """
    store = StateStore(str(tmp_path / "runtime"), runtime_root=str(tmp_path / "runtime"))
    s1 = Session(workspace_root="/w1", spec_id="spec_1")
    store.save_session(s1)
    # Mutate and append again — latest wins.
    s1.status = "closed"
    store.save_session(s1)

    s2 = Session(workspace_root="/w2", spec_id="spec_2")
    store.save_session(s2)

    sessions = {s.session_id: s for s in store.list_sessions()}
    assert sessions[s1.session_id].status == "closed"
    assert sessions[s2.session_id].status == "active"


def test_find_session_by_attachment_matches_active_only(tmp_path):
    """Adoption lookup: find an open session on (workspace_root, spec_id).

    Used by Task 1b run registration to decide whether a new run should
    adopt an existing session or start a fresh one.
    """
    store = StateStore(str(tmp_path / "runtime"), runtime_root=str(tmp_path / "runtime"))
    active = Session(workspace_root="/w", spec_id="spec")
    store.save_session(active)

    closed = Session(workspace_root="/w", spec_id="spec")
    closed.status = "closed"
    store.save_session(closed)

    found = store.find_session_by_attachment(workspace_root="/w", spec_id="spec")
    assert found is not None
    assert found.session_id == active.session_id

    not_found = store.find_session_by_attachment(workspace_root="/w", spec_id="other")
    assert not_found is None
