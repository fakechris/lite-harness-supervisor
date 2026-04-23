"""Tests for the shared IM command dispatch layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from supervisor.operator.command_dispatch import (
    AsyncJobPoller,
    CommandAuth,
    CommandResult,
    _run_buttons,
    dispatch_command,
    format_exchange_result,
    format_explanation_result,
    format_inspect_result,
    format_notes_result,
    format_runs_list,
    parse_command,
    resolve_run,
)


# ── CommandAuth ──────────────────────────────────────────────────


class TestCommandAuth:
    def test_empty_allowlist_rejects_all(self):
        auth = CommandAuth()
        assert not auth.is_authorized("chat1")
        assert not auth.is_authorized("chat1", "user1")

    def test_chat_id_match(self):
        auth = CommandAuth(allowed_chat_ids=["c1", "c2"])
        assert auth.is_authorized("c1")
        assert auth.is_authorized("c2")
        assert not auth.is_authorized("c3")

    def test_user_id_match(self):
        auth = CommandAuth(allowed_user_ids=["u1"])
        assert auth.is_authorized("any_chat", "u1")
        assert not auth.is_authorized("any_chat", "u2")
        assert not auth.is_authorized("any_chat")

    def test_chat_or_user_match(self):
        auth = CommandAuth(allowed_chat_ids=["c1"], allowed_user_ids=["u1"])
        assert auth.is_authorized("c1")
        assert auth.is_authorized("other", "u1")
        assert not auth.is_authorized("other", "other")

    def test_int_ids_coerced(self):
        auth = CommandAuth(allowed_chat_ids=[123], allowed_user_ids=[456])
        assert auth.is_authorized("123")
        assert auth.is_authorized("x", "456")


# ── parse_command ────────────────────────────────────────────────


class TestParseCommand:
    def test_basic(self):
        assert parse_command("/inspect abc123") == ("inspect", ["abc123"])

    def test_no_slash(self):
        assert parse_command("inspect abc123") == ("inspect", ["abc123"])

    def test_command_only(self):
        assert parse_command("/runs") == ("runs", [])

    def test_multiple_args(self):
        assert parse_command("/ask abc123 what is this doing") == (
            "ask",
            ["abc123", "what", "is", "this", "doing"],
        )

    def test_strip_bot_name(self):
        assert parse_command("/runs@my_bot") == ("runs", [])
        assert parse_command("/inspect@bot abc") == ("inspect", ["abc"])

    def test_case_insensitive(self):
        assert parse_command("/INSPECT abc") == ("inspect", ["abc"])

    def test_empty(self):
        assert parse_command("") == ("", [])
        assert parse_command("  ") == ("", [])

    def test_slash_only(self):
        assert parse_command("/") == ("", [])


# ── resolve_run ──────────────────────────────────────────────────


class TestResolveRun:
    @patch("supervisor.operator.tui.collect_runs")
    def test_exact_match(self, mock_runs):
        runs = [{"run_id": "run_abc123"}, {"run_id": "run_xyz789"}]
        mock_runs.return_value = runs
        result = resolve_run("run_abc123")
        assert len(result) == 1
        assert result[0]["run_id"] == "run_abc123"

    @patch("supervisor.operator.tui.collect_runs")
    def test_suffix_match(self, mock_runs):
        runs = [{"run_id": "run_abc123"}, {"run_id": "run_xyz789"}]
        mock_runs.return_value = runs
        result = resolve_run("abc123")
        assert len(result) == 1

    @patch("supervisor.operator.tui.collect_runs")
    def test_no_match(self, mock_runs):
        mock_runs.return_value = [{"run_id": "run_abc"}]
        result = resolve_run("zzz")
        assert len(result) == 0

    @patch("supervisor.operator.tui.collect_runs")
    def test_empty_fragment_returns_all(self, mock_runs):
        runs = [{"run_id": "a"}, {"run_id": "b"}]
        mock_runs.return_value = runs
        result = resolve_run("")
        assert len(result) == 2


# ── Formatting helpers ───────────────────────────────────────────


class TestFormatting:
    def test_runs_list_empty(self):
        assert "No runs" in format_runs_list([])

    def test_runs_list(self):
        runs = [{"run_id": "run_abc123def456", "top_state": "RUNNING", "tag": "daemon"}]
        text = format_runs_list(runs)
        assert "abc123def456" in text
        assert "RUNNING" in text

    def test_inspect_result(self):
        data = {
            "snapshot": {"run_id": "x", "spec_id": "s", "top_state": "RUNNING", "current_node": "n", "current_attempt": 1, "worktree_root": "/w", "done_nodes": []},
            "timeline": [{"occurred_at": "2026-01-01T00:00", "event_type": "start", "summary": "started"}],
        }
        text = format_inspect_result(data)
        assert "RUNNING" in text
        assert "Timeline" in text

    def test_exchange_result_empty(self):
        assert "no exchange" in format_exchange_result({})

    def test_explanation_result(self):
        result = {"explanation": "The run is doing X", "confidence": 0.9}
        text = format_explanation_result(result)
        assert "doing X" in text
        assert "0.9" in text

    def test_notes_empty(self):
        assert "no notes" in format_notes_result([])

    def test_notes_list(self):
        notes = [{"timestamp": "2026-01-01T00:00:00", "author_run_id": "abc", "content": "test note"}]
        text = format_notes_result(notes)
        assert "test note" in text


# ── dispatch_command ─────────────────────────────────────────────


class TestDispatchCommand:
    @patch("supervisor.operator.tui.collect_runs")
    def test_runs(self, mock_runs):
        mock_runs.return_value = [{"run_id": "r1", "top_state": "RUNNING", "tag": "daemon"}]
        result = dispatch_command("runs", [])
        assert not result.error
        assert "r1" in result.text

    def test_help(self):
        result = dispatch_command("help", [])
        assert "/runs" in result.text
        assert "/inspect" in result.text

    def test_unknown_command(self):
        result = dispatch_command("bogus", [])
        assert result.error
        assert "Unknown" in result.text

    @patch("supervisor.operator.command_dispatch.resolve_run")
    @patch("supervisor.operator.command_dispatch.do_inspect")
    def test_inspect(self, mock_inspect, mock_resolve):
        mock_resolve.return_value = [{"run_id": "run_x", "tag": "daemon", "top_state": "RUNNING", "pane_target": "%1", "worktree": "/tmp", "socket": ""}]
        mock_inspect.return_value = {"snapshot": {"run_id": "run_x", "top_state": "RUNNING"}, "timeline": []}
        result = dispatch_command("inspect", ["run_x"])
        assert not result.error
        assert "RUNNING" in result.text

    def test_inspect_no_args(self):
        result = dispatch_command("inspect", [])
        assert result.error
        assert "Usage" in result.text

    @patch("supervisor.operator.command_dispatch.resolve_run")
    @patch("supervisor.operator.command_dispatch.do_pause")
    def test_pause(self, mock_pause, mock_resolve):
        mock_resolve.return_value = [{"run_id": "run_x", "tag": "daemon", "top_state": "RUNNING", "pane_target": "%1", "worktree": "/tmp", "socket": ""}]
        mock_pause.return_value = {"ok": True}
        result = dispatch_command("pause", ["run_x"])
        assert not result.error
        assert "Paused" in result.text

    @patch("supervisor.operator.command_dispatch.resolve_run")
    @patch("supervisor.operator.command_dispatch.do_resume")
    def test_resume(self, mock_resume, mock_resolve):
        mock_resolve.return_value = [{"run_id": "run_x", "tag": "paused", "top_state": "PAUSED_FOR_HUMAN", "pane_target": "", "worktree": "/tmp", "socket": ""}]
        mock_resume.return_value = {"ok": True}
        result = dispatch_command("resume", ["run_x"])
        assert not result.error
        assert "Resumed" in result.text

    @patch("supervisor.operator.command_dispatch.resolve_run")
    @patch("supervisor.operator.command_dispatch.submit_explain")
    def test_explain_returns_job(self, mock_explain, mock_resolve):
        mock_resolve.return_value = [{"run_id": "run_x", "tag": "daemon", "top_state": "RUNNING", "pane_target": "%1", "worktree": "/tmp", "socket": ""}]
        from supervisor.operator.actions import OperatorJob
        mock_explain.return_value = OperatorJob(job_id="j1", source="local")
        result = dispatch_command("explain", ["run_x"])
        assert result.job is not None
        assert result.job.job_id == "j1"
        assert "Working" in result.text

    def test_note_no_content(self):
        result = dispatch_command("note", ["run_x"])
        assert result.error
        assert "Usage" in result.text

    def test_ask_no_question(self):
        result = dispatch_command("ask", ["run_x"])
        assert result.error
        assert "Usage" in result.text

    @patch("supervisor.operator.command_dispatch.resolve_run")
    def test_ambiguous_run(self, mock_resolve):
        mock_resolve.return_value = [{"run_id": "run_a"}, {"run_id": "run_b"}]
        result = dispatch_command("inspect", ["run"])
        assert result.error
        assert "Ambiguous" in result.text

    @patch("supervisor.operator.command_dispatch.resolve_run")
    def test_run_not_found(self, mock_resolve):
        mock_resolve.return_value = []
        result = dispatch_command("inspect", ["zzz"])
        assert result.error
        assert "not found" in result.text


# ── /escalate ────────────────────────────────────────────────────


class TestEscalateCommand:
    def _seed(self, tmp_path, run_id, events):
        from supervisor.operator.api import append_timeline_event
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text('{"run_id": "' + run_id + '"}')
        log = run_dir / "session_log.jsonl"
        for et, payload in events:
            append_timeline_event(log, run_id, et, payload)
        return {
            "run_id": run_id, "tag": "local", "top_state": "COMPLETED",
            "pane_target": "", "worktree": str(tmp_path), "socket": "",
        }

    def test_missing_args(self):
        result = dispatch_command("escalate", [])
        assert result.error
        assert "Usage" in result.text

    @patch("supervisor.operator.command_dispatch.do_escalate_clarification")
    @patch("supervisor.operator.command_dispatch.resolve_run")
    def test_uses_last_clarification_from_log(
        self, mock_resolve, mock_escalate, tmp_path,
    ):
        run = self._seed(tmp_path, "run_esc_a", [
            ("clarification_request", {"question": "q1"}),
            ("clarification_response", {
                "question": "why is the gate failing?",
                "confidence": 0.2,
                "escalation_recommended": True,
            }),
        ])
        mock_resolve.return_value = [run]
        mock_escalate.return_value = {"escalation_id": "abcd1234efgh5678", "source": "local"}

        result = dispatch_command("escalate", ["run_esc_a"])
        assert not result.error
        assert "Escalated" in result.text
        assert "abcd1234efgh" in result.text  # id prefix
        kwargs = mock_escalate.call_args.kwargs
        args = mock_escalate.call_args.args
        assert args[1] == "why is the gate failing?"
        assert kwargs["confidence"] == 0.2
        assert kwargs["reason"] == "im_operator"

    @patch("supervisor.operator.command_dispatch.do_escalate_clarification")
    @patch("supervisor.operator.command_dispatch.resolve_run")
    def test_explicit_question_overrides_log(
        self, mock_resolve, mock_escalate, tmp_path,
    ):
        run = self._seed(tmp_path, "run_esc_b", [
            ("clarification_response", {"question": "old", "confidence": 0.1}),
        ])
        mock_resolve.return_value = [run]
        mock_escalate.return_value = {"escalation_id": "deadbeef00000000", "source": "local"}

        result = dispatch_command(
            "escalate", ["run_esc_b", "is", "this", "safe?"],
        )
        assert not result.error
        args = mock_escalate.call_args.args
        kwargs = mock_escalate.call_args.kwargs
        assert args[1] == "is this safe?"
        assert kwargs["confidence"] is None  # explicit override has no confidence

    @patch("supervisor.operator.command_dispatch.resolve_run")
    def test_no_prior_clarification_errors(self, mock_resolve, tmp_path):
        run = self._seed(tmp_path, "run_esc_c", [])
        mock_resolve.return_value = [run]
        result = dispatch_command("escalate", ["run_esc_c"])
        assert result.error
        assert "No prior clarification" in result.text


# ── AsyncJobPoller ───────────────────────────────────────────────


class TestAsyncJobPoller:
    def test_track_and_complete(self):
        from supervisor.operator.actions import OperatorJob

        poller = AsyncJobPoller(poll_interval=0.05, timeout=5.0)
        ctx = MagicMock()
        job = OperatorJob(job_id="test1", source="local")
        completed = {}

        def on_complete(result):
            completed["result"] = result

        with patch("supervisor.operator.command_dispatch.poll_job") as mock_poll:
            mock_poll.return_value = {"status": "completed", "result": {"explanation": "done"}}
            poller.track(ctx, job, on_complete)
            import time
            time.sleep(0.3)
            poller.stop()

        assert "result" in completed
        assert completed["result"]["status"] == "completed"

    def test_timeout(self):
        from supervisor.operator.actions import OperatorJob

        poller = AsyncJobPoller(poll_interval=0.05, timeout=0.1)
        ctx = MagicMock()
        job = OperatorJob(job_id="test2", source="local")
        completed = {}

        with patch("supervisor.operator.command_dispatch.poll_job") as mock_poll:
            mock_poll.return_value = {"status": "pending"}
            poller.track(ctx, job, lambda r: completed.update({"result": r}))
            import time
            time.sleep(0.5)
            poller.stop()

        assert completed.get("result", {}).get("status") == "failed"
        assert "timeout" in completed.get("result", {}).get("error", "")
