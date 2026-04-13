"""E2E scenario tests covering all 7 surface x agent combinations.

Each scenario traces the full data flow:
  Attach → Observe → Parse → Verify(cwd) → Inject
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.domain.enums import TopState
from supervisor.domain.models import WorkerProfile


# ---------------------------------------------------------------------------
# Mock surfaces
# ---------------------------------------------------------------------------

class MockSurface:
    """Mock that exactly matches the working pattern from test_sidecar_loop.py."""

    is_observation_only = False

    def __init__(self, outputs: list[str], cwd: str = ""):
        self._outputs = list(outputs)
        self._index = 0
        self._cwd = cwd
        self._read_done = False
        self.injected: list[str] = []

    def read(self, lines: int = 100) -> str:
        self._read_done = True
        if self._index < len(self._outputs):
            text = self._outputs[self._index]
            self._index += 1
            return text
        return ""

    def inject(self, text: str) -> None:
        self.injected.append(text)
        self._read_done = False

    def current_cwd(self) -> str:
        return self._cwd

    def session_id(self) -> str:
        return "mock"


class MockObservationOnlySurface(MockSurface):
    """JSONL-like: observation-only, inject is a no-op."""
    is_observation_only = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stop_event(timeout_sec=5):
    """Create a stop event that fires after timeout_sec as safety net."""
    import threading, time
    stop = threading.Event()
    def _fire():
        time.sleep(timeout_sec)
        stop.set()
    threading.Thread(target=_fire, daemon=True).start()
    return stop


def _make_checkpoint(status, node, summary):
    return (
        f"<checkpoint>\n"
        f"status: {status}\n"
        f"current_node: {node}\n"
        f"summary: {summary}\n"
        f"evidence:\n  - ran: echo ok\n"
        f"candidate_next_actions:\n  - continue\n"
        f"needs:\n  - none\n"
        f"question_for_supervisor:\n  - none\n"
        f"</checkpoint>\n"
    )


# ---------------------------------------------------------------------------
# Scenario 1: tmux + Codex (golden path)
# ---------------------------------------------------------------------------

class TestScenarioTmuxCodex:
    """Golden path: full 3-step linear plan via tmux surface."""

    def test_full_e2e(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        loop = SupervisorLoop(store)

        terminal = MockSurface([
            _make_checkpoint("step_done", "write_test", "wrote tests"),
            _make_checkpoint("step_done", "implement_feature", "implemented"),
            _make_checkpoint("step_done", "final_verify", "verified"),
        ])

        final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event())
        assert final.top_state == TopState.COMPLETED
        assert set(final.done_node_ids) == {"write_test", "implement_feature", "final_verify"}
        assert len(terminal.injected) >= 2

    def test_retry_on_verification_failure(self, tmp_path):
        """First checkpoint fails verification (file missing), retry succeeds."""
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        loop = SupervisorLoop(store)

        # File does NOT exist initially — first verification will fail
        # Surface that creates the file after first read (simulating agent fixing it)
        class RetryMockSurface(MockSurface):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self._created_file = False
            def read(self, lines=100):
                text = super().read(lines)
                if not self._created_file and self._index >= 2:
                    # After second checkpoint, create the artifact
                    (tmp_path / "tests").mkdir(exist_ok=True)
                    (tmp_path / "tests" / "test_example.py").write_text("pass")
                    self._created_file = True
                return text

        terminal = RetryMockSurface([
            _make_checkpoint("step_done", "write_test", "first try"),
            _make_checkpoint("step_done", "write_test", "second try fixed"),
            _make_checkpoint("step_done", "implement_feature", "impl done"),
            _make_checkpoint("step_done", "final_verify", "all done"),
        ], cwd=str(tmp_path))

        final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event())
        assert final.top_state == TopState.COMPLETED
        # Verify retry actually happened
        assert state.retry_budget.used_global >= 1

    def test_escalation_on_blocked(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        loop = SupervisorLoop(store)

        terminal = MockSurface([
            _make_checkpoint("blocked", "write_test", "need credentials"),
        ])

        final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event())
        assert final.top_state == TopState.PAUSED_FOR_HUMAN


# ---------------------------------------------------------------------------
# Scenario 2: tmux + Claude Code (same as Codex for supervisor)
# ---------------------------------------------------------------------------

class TestScenarioTmuxClaude:
    def test_full_e2e(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        loop = SupervisorLoop(store)

        # Create artifact so write_test verifier passes
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_example.py").write_text("pass")

        terminal = MockSurface([
            _make_checkpoint("step_done", "write_test", "wrote tests"),
            _make_checkpoint("step_done", "implement_feature", "implemented"),
            _make_checkpoint("step_done", "final_verify", "verified"),
        ], cwd=str(tmp_path))

        final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event())
        assert final.top_state == TopState.COMPLETED


# ---------------------------------------------------------------------------
# Scenario 5: JSONL observation-only
# ---------------------------------------------------------------------------

class TestScenarioJsonlObservationOnly:
    def test_checkpoint_parsed_from_jsonl(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        loop = SupervisorLoop(store)

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_example.py").write_text("pass")

        terminal = MockObservationOnlySurface([
            _make_checkpoint("step_done", "write_test", "wrote tests"),
            _make_checkpoint("step_done", "implement_feature", "implemented"),
            _make_checkpoint("step_done", "final_verify", "verified"),
        ])

        final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event())
        assert final.top_state == TopState.PAUSED_FOR_HUMAN
        assert "observation-only surface cannot confirm instruction delivery" in final.human_escalations[-1]["reason"]

    def test_observation_only_flag_respected(self, tmp_path):
        """Observation-only surfaces should not pretend inject delivery succeeded."""
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        loop = SupervisorLoop(store)

        terminal = MockObservationOnlySurface([
            _make_checkpoint("step_done", "write_test", "wrote tests"),
        ])

        final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event())
        assert final.top_state == TopState.PAUSED_FOR_HUMAN
        assert len(terminal.injected) >= 1

    def test_observation_only_does_not_require_spec_node_ids(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        loop = SupervisorLoop(store)

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_example.py").write_text("pass")

        terminal = MockObservationOnlySurface([
            _make_checkpoint("step_done", "agent_step_a", "wrote tests"),
            _make_checkpoint("step_done", "agent_step_b", "implemented"),
            _make_checkpoint("step_done", "agent_step_c", "verified"),
        ])

        final = loop.run_sidecar(
            spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event()
        )

        assert final.top_state == TopState.PAUSED_FOR_HUMAN

    def test_stale_done_node_checkpoint_escalates_in_observation_only_mode(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        loop = SupervisorLoop(store)

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_example.py").write_text("pass")

        terminal = MockObservationOnlySurface([
            _make_checkpoint("step_done", "write_test", "wrote tests"),
            _make_checkpoint("working", "write_test", "still on old node"),
        ])

        final = loop.run_sidecar(
            spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event()
        )

        assert final.top_state == TopState.PAUSED_FOR_HUMAN
        assert any("observation-only" in e.get("reason", "") for e in final.human_escalations)


# ---------------------------------------------------------------------------
# Scenario 6: cwd fallback
# ---------------------------------------------------------------------------

class TestScenarioCwdFallback:
    def test_empty_cwd_falls_back_to_workspace_root(self, tmp_path):
        """When surface returns empty cwd, verifier uses workspace_root."""
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        loop = SupervisorLoop(store)

        # Surface returns empty cwd — should fall back to workspace_root
        terminal = MockSurface([
            _make_checkpoint("step_done", "write_test", "wrote tests"),
            _make_checkpoint("step_done", "implement_feature", "implemented"),
            _make_checkpoint("step_done", "final_verify", "verified"),
        ], cwd="")  # empty cwd!

        # Create artifact in workspace_root so verifier finds it via fallback
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_example.py").write_text("pass")

        final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event())
        assert final.top_state == TopState.COMPLETED


# ---------------------------------------------------------------------------
# Scenario 7: supervision policy integration
# ---------------------------------------------------------------------------

class TestScenarioSupervisionPolicy:
    def test_strong_worker_gets_minimal_instruction(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        worker = WorkerProfile(trust_level="high", model_name="claude-opus-4-6")
        loop = SupervisorLoop(store, worker_profile=worker)

        terminal = MockSurface([
            _make_checkpoint("step_done", "write_test", "done"),
            _make_checkpoint("step_done", "implement_feature", "done"),
            _make_checkpoint("step_done", "final_verify", "done"),
        ], cwd=str(tmp_path))

        # Create artifact for verifier
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_example.py").write_text("pass")

        loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event())
        # Must have injected at least once
        assert len(terminal.injected) >= 1, "no injections happened — test is vacuous"
        # strict_verifier mode: injections should NOT contain [DIRECTIVE]
        for inj in terminal.injected:
            assert "[DIRECTIVE]" not in inj

    def test_weak_worker_gets_directive(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec, workspace_root=str(tmp_path))
        worker = WorkerProfile(trust_level="low", model_name="minimax")
        loop = SupervisorLoop(store, worker_profile=worker)

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_example.py").write_text("pass")

        terminal = MockSurface([
            _make_checkpoint("step_done", "write_test", "done"),
            _make_checkpoint("step_done", "implement_feature", "done"),
            _make_checkpoint("step_done", "final_verify", "done"),
        ], cwd=str(tmp_path))

        loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_make_stop_event())
        assert len(terminal.injected) >= 1, "no injections happened — test is vacuous"
        # collaborative_reviewer mode: should mention approach
        assert any("approach" in inj.lower() or "risk" in inj.lower()
                       for inj in terminal.injected)
