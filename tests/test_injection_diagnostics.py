"""Tests for defensive injection handling."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from supervisor.domain.enums import TopState
from supervisor.loop import SupervisorLoop
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.terminal.adapter import InjectionConfirmationError, TerminalAdapter


def _mock_run(stdout="", returncode=0, stderr=""):
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


@patch("subprocess.run")
def test_terminal_adapter_raises_when_text_appears_stuck_in_tail(mock_run):
    snapshots = [
        _mock_run(stdout="before\n"),
        _mock_run(stdout="before\n"),
        _mock_run(stdout="before\nqueued instruction\n"),
        _mock_run(stdout="before\nqueued instruction\n"),
        _mock_run(stdout="before\nqueued instruction\n"),
        _mock_run(stdout="before\nqueued instruction\n"),
        _mock_run(stdout="before\nqueued instruction\n"),
    ]
    mock_run.side_effect = lambda *args, **kwargs: snapshots.pop(0) if snapshots else _mock_run(
        stdout="before\nqueued instruction\n"
    )

    adapter = TerminalAdapter("%0")
    adapter.read()
    with pytest.raises(InjectionConfirmationError):
        adapter.inject("queued instruction")


@patch("time.sleep", return_value=None)
@patch("subprocess.run")
def test_terminal_adapter_accepts_wrapped_prompt_when_working_started(mock_run, _sleep):
    instruction = (
        "Build the admin-side modules for templates, rules, permissions, "
        "statistics, and export workflows."
    )
    snapshots = [
        _mock_run(stdout="before\n"),
        _mock_run(stdout="before\n"),
        _mock_run(
            stdout=(
                "prior output\n"
                "› Build the admin-side modules for templates, rules,\n"
                "  permissions, statistics, and export workflows.\n"
                "• Working (12s • esc to interrupt)\n"
                "\n"
                "  gpt-5.4 high\n"
            )
        ),
    ]
    mock_run.side_effect = lambda *args, **kwargs: snapshots.pop(0) if snapshots else _mock_run(
        stdout=(
            "prior output\n"
            "› Build the admin-side modules for templates, rules,\n"
            "  permissions, statistics, and export workflows.\n"
            "• Working (12s • esc to interrupt)\n"
            "\n"
            "  gpt-5.4 high\n"
        )
    )

    adapter = TerminalAdapter("%0")
    adapter.read()
    adapter.inject(instruction)


@patch("time.sleep", return_value=None)
@patch("subprocess.run")
def test_terminal_adapter_retries_enter_when_submit_is_not_confirmed(mock_run, _sleep):
    instruction = (
        "Implement notification orchestration, mock/dev DingTalk integrations, "
        "AI service stubs or implementations, and local development flows."
    )
    stuck_snapshot = _mock_run(
        stdout=(
            "prior output\n"
            "› Implement notification orchestration, mock/dev DingTalk integrations,\n"
            "  AI service stubs or implementations, and local development flows.\n"
            "\n"
            "  gpt-5.4 high\n"
        )
    )
    working_snapshot = _mock_run(
        stdout=(
            "prior output\n"
            "› Implement notification orchestration, mock/dev DingTalk integrations,\n"
            "  AI service stubs or implementations, and local development flows.\n"
            "• Working (5s • esc to interrupt)\n"
            "\n"
            "  gpt-5.4 high\n"
        )
    )
    snapshots = iter([
        _mock_run(stdout="before\n"),
        *([stuck_snapshot] * 10),
        working_snapshot,
    ])

    def side_effect(cmd, **kwargs):
        if "capture-pane" in cmd:
            return next(snapshots, working_snapshot)
        return _mock_run(stdout="")

    mock_run.side_effect = side_effect

    adapter = TerminalAdapter("%0")
    adapter.read()
    adapter.inject(instruction)

    enter_calls = [
        call for call in mock_run.call_args_list
        if "send-keys" in call.args[0] and "Enter" in call.args[0]
    ]
    assert len(enter_calls) == 2


@patch("time.sleep", return_value=None)
@patch("subprocess.run")
def test_terminal_adapter_raises_when_prompt_prefix_is_still_visible(mock_run, _sleep):
    instruction = (
        "Replace in-memory business-state dependencies with persistent "
        "database-backed services and real browser-facing write closures. "
        "Stay on current_node: step_2_persistence_and_write_closure. "
        "After meaningful progress, output a checkpoint block exactly like: "
        "<checkpoint> status: working | blocked | step_done | workflow_done "
        "current_node: step_2_persistence_and_write_closure summary: "
        "<one-line description> evidence: ... candidate_next_actions: ... "
        "needs: - none question_for_supervisor: - none </checkpoint>"
    )
    partial_prompt_snapshot = _mock_run(
        stdout=(
            "previous output\n"
            "› Replace in-memory business-state dependencies with persistent\n"
            "  database-backed services and real browser-facing write closures.\n"
            "  Stay on current_node: step_2_persistence_and_write_closure.\n"
            "\n"
            "  gpt-5.4 high\n"
        )
    )
    snapshots = iter([
        _mock_run(stdout="before\n"),
        *([partial_prompt_snapshot] * 20),
    ])

    def side_effect(cmd, **kwargs):
        if "capture-pane" in cmd:
            return next(snapshots, partial_prompt_snapshot)
        return _mock_run(stdout="")

    mock_run.side_effect = side_effect

    adapter = TerminalAdapter("%0")
    adapter.read()
    with pytest.raises(InjectionConfirmationError):
        adapter.inject(instruction)


@patch("time.sleep", return_value=None)
@patch("subprocess.run")
def test_terminal_adapter_rejects_delayed_prompt_reappearance(mock_run, _sleep):
    instruction = (
        "Replace in-memory business-state dependencies with persistent "
        "database-backed services and real browser-facing write closures. "
        "Continue with the highest-priority remaining action in the current node. "
        "Stay on current_node: step_2_persistence_and_write_closure."
    )
    partial_prompt_snapshot = _mock_run(
        stdout=(
            "previous output\n"
            "› Replace in-memory business-state dependencies with persistent\n"
            "  database-backed services and real browser-facing write closures.\n"
            "  Continue with the highest-priority remaining action in the current node.\n"
            "  Stay on current_node: step_2_persistence_and_write_closure.\n"
        )
    )
    snapshots = iter([
        _mock_run(stdout="before\n"),
        _mock_run(stdout="before\n"),  # too-early clean read should not end confirmation
        partial_prompt_snapshot,
        partial_prompt_snapshot,
        partial_prompt_snapshot,
        partial_prompt_snapshot,
        partial_prompt_snapshot,
        partial_prompt_snapshot,
        partial_prompt_snapshot,
        partial_prompt_snapshot,
        partial_prompt_snapshot,
        partial_prompt_snapshot,
    ])

    def side_effect(cmd, **kwargs):
        if "capture-pane" in cmd:
            return next(snapshots, partial_prompt_snapshot)
        return _mock_run(stdout="")

    mock_run.side_effect = side_effect

    adapter = TerminalAdapter("%0")
    adapter.read()
    with pytest.raises(InjectionConfirmationError):
        adapter.inject(instruction)


class _FailingInjectTerminal:
    def __init__(self):
        self._read_done = False

    def read(self, lines: int = 100) -> str:
        self._read_done = True
        return ""

    def inject(self, text: str) -> None:
        assert self._read_done is True
        self._read_done = False
        raise InjectionConfirmationError("submit not confirmed")


def test_sidecar_pauses_for_human_when_injection_confirmation_fails(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    final = loop.run_sidecar(spec, state, _FailingInjectTerminal(), poll_interval=0, read_lines=50)

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert final.human_escalations
    assert "submit not confirmed" in final.human_escalations[-1]["reason"]

    session_log = Path(store.session_log_path)
    lines = [json.loads(line) for line in session_log.read_text().splitlines()]
    assert any(event["event_type"] == "injection_failed" for event in lines)
