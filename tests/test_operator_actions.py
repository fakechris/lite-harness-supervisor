"""Tests for unified operator actions."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from supervisor.operator.actions import (
    ActionUnavailable,
    OperatorJob,
    _local_jobs,
    build_explainer_context,
    do_escalate_clarification,
    do_exchange,
    do_inspect,
    do_note_add,
    do_note_list,
    do_pause,
    do_resume,
    poll_job,
    submit_clarification,
    submit_drift,
    submit_explain,
    submit_explain_exchange,
)
from supervisor.operator.run_context import ActionMode, RunContext


def _make_ctx(tag="daemon", has_daemon=True, **overrides):
    """Build a RunContext with controlled capabilities."""
    defaults = {
        "run_id": "run_test123",
        "worktree": "/tmp/ws",
        "tag": tag,
        "top_state": "RUNNING",
        "pane_target": "%5",
        "socket": "/tmp/sock" if tag == "daemon" else "",
        "spec_path": "/specs/test.yaml",
        "config_path": "/tmp/ws/.supervisor/config.yaml",
    }
    defaults.update(overrides)
    ctx = RunContext(**defaults)
    return ctx


# ── do_inspect ───────────────────────────────────────────────────


class TestDoInspect:
    def test_daemon_path(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.get_snapshot.return_value = {"ok": True, "run_id": "run_test123"}
        mock_client.get_timeline.return_value = {"ok": True, "events": [{"seq": 1}]}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            result = do_inspect(ctx)
        assert "snapshot" in result
        assert result["timeline"] == [{"seq": 1}]

    def test_local_path(self, tmp_path):
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_local"
        run_dir.mkdir(parents=True)
        state = {"run_id": "run_local", "top_state": "COMPLETED", "spec_id": "s"}
        (run_dir / "state.json").write_text(json.dumps(state))

        ctx = _make_ctx(
            tag="completed",
            run_id="run_local",
            worktree=str(tmp_path),
            socket="",
            state_dir=run_dir,
            state_path=run_dir / "state.json",
            session_log_path=run_dir / "session_log.jsonl",
        )
        with patch.object(ctx, "_has_daemon", return_value=False):
            result = do_inspect(ctx)
        assert result["snapshot"]["run_id"] == "run_local"

    def test_missing_state_returns_empty(self, tmp_path):
        ctx = _make_ctx(
            tag="completed",
            run_id="run_gone",
            worktree=str(tmp_path),
            socket="",
            state_dir=tmp_path / "no_exist",
            state_path=tmp_path / "no_exist" / "state.json",
            session_log_path=tmp_path / "no_exist" / "session_log.jsonl",
        )
        with patch.object(ctx, "_has_daemon", return_value=False):
            result = do_inspect(ctx)
        assert result["snapshot"] == {}


# ── do_exchange ──────────────────────────────────────────────────


class TestDoExchange:
    def test_daemon_path(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.get_exchange.return_value = {"run_id": "run_test123", "ok": True}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            result = do_exchange(ctx)
        assert result["run_id"] == "run_test123"


# ── do_pause / do_resume ─────────────────────────────────────────


class TestDoPause:
    def test_daemon_pauses(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.stop_run.return_value = {"ok": True}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            result = do_pause(ctx)
        assert result["ok"]

    def test_foreground_raises(self):
        ctx = _make_ctx(tag="foreground", socket="")
        with patch.object(ctx, "_has_daemon", return_value=False):
            with pytest.raises(ActionUnavailable, match="foreground"):
                do_pause(ctx)

    def test_completed_raises(self):
        ctx = _make_ctx(tag="completed", socket="")
        with patch.object(ctx, "_has_daemon", return_value=False):
            with pytest.raises(ActionUnavailable, match="completed"):
                do_pause(ctx)


class TestDoResume:
    def test_daemon_resumes(self):
        ctx = _make_ctx(tag="paused", top_state="PAUSED_FOR_HUMAN")
        mock_client = MagicMock()
        mock_client.resume.return_value = {"ok": True}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            result = do_resume(ctx)
        assert result["ok"]

    def test_completed_raises(self):
        ctx = _make_ctx(tag="completed", socket="")
        with patch.object(ctx, "_has_daemon", return_value=False):
            with pytest.raises(ActionUnavailable, match="completed"):
                do_resume(ctx)

    def test_no_spec_path_raises(self):
        ctx = _make_ctx(tag="paused", spec_path="")
        mock_client = MagicMock()
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            with pytest.raises(ActionUnavailable, match="spec_path"):
                do_resume(ctx)

    def test_no_pane_target_raises(self):
        ctx = _make_ctx(tag="paused", pane_target="?")
        mock_client = MagicMock()
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            with pytest.raises(ActionUnavailable, match="pane_target"):
                do_resume(ctx)

    def test_auto_start_calls_ensure_daemon(self):
        ctx = _make_ctx(tag="orphaned", socket="")
        mock_client = MagicMock()
        mock_client.resume.return_value = {"ok": True}
        with patch.object(ctx, "_has_daemon", return_value=False):
            with patch.object(ctx, "ensure_daemon", return_value=mock_client):
                result = do_resume(ctx)
        assert result["ok"]


# ── do_note_add / do_note_list ───────────────────────────────────


class TestNotes:
    def test_note_add_daemon(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.note_add.return_value = {"ok": True}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            result = do_note_add(ctx, "hello")
        assert result["ok"]
        mock_client.note_add.assert_called_once()

    def test_note_add_unavailable_for_completed(self):
        ctx = _make_ctx(tag="completed", socket="")
        with patch.object(ctx, "_has_daemon", return_value=False):
            with pytest.raises(ActionUnavailable, match="no daemon"):
                do_note_add(ctx, "hello")

    def test_note_list_daemon(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.note_list.return_value = {"notes": [{"content": "a"}]}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            result = do_note_list(ctx)
        assert len(result) == 1

    def test_note_list_unavailable_for_foreground(self):
        ctx = _make_ctx(tag="foreground", socket="")
        with patch.object(ctx, "_has_daemon", return_value=False):
            with pytest.raises(ActionUnavailable, match="no daemon"):
                do_note_list(ctx)


# ── submit_explain / submit_drift / submit_explain_exchange ──────


class TestSubmitExplain:
    def test_daemon_path_returns_job(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.explain_run.return_value = {"job_id": "job_abc", "ok": True}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            job = submit_explain(ctx, language="en")
        assert job.source == "daemon"
        assert job.job_id == "job_abc"

    def test_local_path_returns_job(self, tmp_path):
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_local"
        run_dir.mkdir(parents=True)
        state = {"run_id": "run_local", "top_state": "COMPLETED"}
        (run_dir / "state.json").write_text(json.dumps(state))

        ctx = _make_ctx(
            tag="completed",
            run_id="run_local",
            worktree=str(tmp_path),
            socket="",
            state_dir=run_dir,
            state_path=run_dir / "state.json",
            session_log_path=run_dir / "session_log.jsonl",
        )
        with patch.object(ctx, "_has_daemon", return_value=False):
            with patch.object(ctx, "load_config") as mock_cfg:
                mock_cfg.return_value = MagicMock(
                    explainer_model=None,
                    explainer_temperature=0.3,
                    explainer_max_tokens=1024,
                )
                job = submit_explain(ctx, language="zh")
        assert job.source == "local"
        assert job.job_id.startswith("job_")

        # Wait for background thread to complete
        for _ in range(50):
            j = _local_jobs.get(job.job_id)
            if j and j.status in ("completed", "failed"):
                break
            time.sleep(0.05)
        j = _local_jobs.get(job.job_id)
        assert j.status == "completed"


class TestSubmitDrift:
    def test_daemon_path(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.assess_drift.return_value = {"job_id": "job_d1", "ok": True}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            job = submit_drift(ctx)
        assert job.source == "daemon"
        assert job.job_id == "job_d1"


class TestSubmitExplainExchange:
    def test_daemon_path(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.explain_exchange.return_value = {"job_id": "job_x1", "ok": True}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            job = submit_explain_exchange(ctx)
        assert job.source == "daemon"
        assert job.job_id == "job_x1"


# ── poll_job ─────────────────────────────────────────────────────


class TestPollJob:
    def test_daemon_poll(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.get_job.return_value = {"status": "completed", "result": {"ok": True}}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            result = poll_job(ctx, OperatorJob(job_id="job_abc", source="daemon"))
        assert result["status"] == "completed"

    def test_local_poll(self):
        # Submit a quick local job directly
        job_id = _local_jobs.submit("test", lambda: {"answer": 42})
        for _ in range(50):
            j = _local_jobs.get(job_id)
            if j and j.status in ("completed", "failed"):
                break
            time.sleep(0.05)

        ctx = _make_ctx(tag="completed")
        result = poll_job(ctx, OperatorJob(job_id=job_id, source="local"))
        assert result["status"] == "completed"

    def test_missing_local_job(self):
        ctx = _make_ctx(tag="completed")
        result = poll_job(ctx, OperatorJob(job_id="job_nonexistent", source="local"))
        assert result["status"] == "failed"

    def test_daemon_unreachable(self):
        ctx = _make_ctx(tag="daemon")
        with patch.object(ctx, "get_client", return_value=None):
            result = poll_job(ctx, OperatorJob(job_id="job_abc", source="daemon"))
        assert result["status"] == "failed"
        assert "unreachable" in result["error"]


# ── build_explainer_context ──────────────────────────────────────


class TestBuildExplainerContext:
    def test_includes_run_state_and_events(self, tmp_path):
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_ctx"
        run_dir.mkdir(parents=True)
        state = {"run_id": "run_ctx", "top_state": "RUNNING", "spec_path": ""}
        (run_dir / "state.json").write_text(json.dumps(state))

        ctx = _make_ctx(
            tag="completed", run_id="run_ctx", worktree=str(tmp_path),
            socket="", state_dir=run_dir, state_path=run_dir / "state.json",
            session_log_path=run_dir / "session_log.jsonl",
        )
        with patch.object(ctx, "_has_daemon", return_value=False):
            result = build_explainer_context(ctx, language="en")
        assert result["run_state"]["run_id"] == "run_ctx"
        assert "recent_events" in result
        assert "codebase_signals" in result
        assert result.get("language") == "en"

    def test_loads_spec_context_when_available(self, tmp_path):
        """Verify spec_context is populated from spec file."""
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_spec"
        run_dir.mkdir(parents=True)

        # Create a minimal spec file
        spec_dir = tmp_path / ".supervisor" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "test.yaml"
        spec_file.write_text(
            "kind: linear_plan\n"
            "id: test-spec\n"
            "goal: test goal\n"
            "steps:\n"
            "  - id: step_1\n"
            "    type: instruct\n"
            "    objective: do something\n"
            "    instruction: do it\n"
        )

        state = {
            "run_id": "run_spec", "top_state": "RUNNING",
            "spec_path": str(spec_file),
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        ctx = _make_ctx(
            tag="completed", run_id="run_spec", worktree=str(tmp_path),
            socket="", spec_path=str(spec_file),
            state_dir=run_dir, state_path=run_dir / "state.json",
            session_log_path=run_dir / "session_log.jsonl",
        )
        with patch.object(ctx, "_has_daemon", return_value=False):
            result = build_explainer_context(ctx)
        assert "spec_context" in result
        assert result["spec_context"]["id"] == "test-spec"
        assert result["spec_context"]["goal"] == "test goal"
        assert len(result["spec_context"]["nodes"]) == 1

    def test_missing_spec_still_works(self, tmp_path):
        """Context building doesn't fail when spec file is missing."""
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_nospec"
        run_dir.mkdir(parents=True)
        state = {
            "run_id": "run_nospec", "top_state": "COMPLETED",
            "spec_path": "/nonexistent/spec.yaml",
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        ctx = _make_ctx(
            tag="completed", run_id="run_nospec", worktree=str(tmp_path),
            socket="", state_dir=run_dir, state_path=run_dir / "state.json",
            session_log_path=run_dir / "session_log.jsonl",
        )
        with patch.object(ctx, "_has_daemon", return_value=False):
            result = build_explainer_context(ctx)
        # Should succeed without spec_context
        assert "run_state" in result
        assert "spec_context" not in result


# ── submit_clarification ─────────────────────────────────────────


class TestSubmitClarification:
    def test_daemon_path(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.request_clarification.return_value = {"job_id": "job_c1", "ok": True}
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            job = submit_clarification(ctx, "why is this paused?")
        assert job.source == "daemon"
        assert job.job_id == "job_c1"

    def test_local_path(self, tmp_path):
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_c"
        run_dir.mkdir(parents=True)
        state = {"run_id": "run_c", "top_state": "COMPLETED"}
        (run_dir / "state.json").write_text(json.dumps(state))

        ctx = _make_ctx(
            tag="completed", run_id="run_c", worktree=str(tmp_path),
            socket="", state_dir=run_dir, state_path=run_dir / "state.json",
            session_log_path=run_dir / "session_log.jsonl",
        )
        with patch.object(ctx, "_has_daemon", return_value=False):
            with patch.object(ctx, "load_config") as mock_cfg:
                mock_cfg.return_value = MagicMock(
                    explainer_model=None,
                    explainer_temperature=0.3,
                    explainer_max_tokens=1024,
                    deep_explainer_model=None,
                    deep_explainer_temperature=0.2,
                    deep_explainer_max_tokens=2048,
                    clarification_escalation_confidence=0.4,
                )
                job = submit_clarification(ctx, "what happened?", language="zh")
        assert job.source == "local"

        # Wait for completion
        for _ in range(50):
            j = _local_jobs.get(job.job_id)
            if j and j.status in ("completed", "failed"):
                break
            time.sleep(0.05)
        j = _local_jobs.get(job.job_id)
        assert j.status == "completed"
        # Stub mode should include the question in the answer
        assert "what happened?" in j.result.get("answer", "")
        # Stub confidence (0.1) is below default threshold (0.4) → escalation
        assert j.result.get("escalation_recommended") is True

        # Verify clarification events written to session_log
        session_log = run_dir / "session_log.jsonl"
        assert session_log.exists()
        events = [json.loads(line) for line in session_log.read_text().strip().splitlines()]
        types = [e["event_type"] for e in events]
        assert types == [
            "clarification_request",
            "explainer_answer",
            "clarification_response",
            "clarification_escalation_recommended",
        ]
        assert events[0]["payload"]["question"] == "what happened?"
        assert events[1]["payload"]["source"] == "explainer"
        assert "what happened?" in events[1]["payload"]["answer"]
        assert events[2]["payload"]["source"] == "explainer"
        assert events[2]["payload"]["escalation_recommended"] is True
        assert events[3]["payload"]["threshold"] == 0.4


class TestClarificationSummarizers:
    def test_request_summarizer(self):
        from supervisor.operator.api import _summarize_event
        summary = _summarize_event("clarification_request", {"question": "why paused?"})
        assert "Q:" in summary
        assert "why paused?" in summary

    def test_response_summarizer(self):
        from supervisor.operator.api import _summarize_event
        summary = _summarize_event("clarification_response", {"answer": "agent idle"})
        assert "A:" in summary
        assert "agent idle" in summary


class TestEscalateClarification:
    def test_daemon_path(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.escalate_clarification.return_value = {
            "ok": True, "escalation_id": "abc123def456abcd",
        }
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            resp = do_escalate_clarification(
                ctx, "why is verification stuck?",
                language="en", reason="tui_low_confidence",
                operator="op1", confidence=0.15,
            )
        assert resp["source"] == "daemon"
        assert resp["escalation_id"] == "abc123def456abcd"
        mock_client.escalate_clarification.assert_called_once_with(
            "run_test123", "why is verification stuck?",
            language="en", reason="tui_low_confidence",
            operator="op1", confidence=0.15,
        )

    def test_daemon_error_becomes_unavailable(self):
        ctx = _make_ctx(tag="daemon")
        mock_client = MagicMock()
        mock_client.escalate_clarification.return_value = {
            "ok": False, "error": "run not found",
        }
        mock_client.is_running.return_value = True
        with patch.object(ctx, "get_client", return_value=mock_client):
            with pytest.raises(ActionUnavailable, match="run not found"):
                do_escalate_clarification(ctx, "q?")

    def test_local_path_writes_event(self, tmp_path):
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_e"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(json.dumps({"run_id": "run_e"}))

        ctx = _make_ctx(
            tag="completed", run_id="run_e", worktree=str(tmp_path),
            socket="", state_dir=run_dir, state_path=run_dir / "state.json",
            session_log_path=run_dir / "session_log.jsonl",
        )
        with patch.object(ctx, "_has_daemon", return_value=False):
            resp = do_escalate_clarification(
                ctx, "is the migration safe?",
                language="zh", reason="im_operator",
                operator="song", confidence=0.2,
            )
        assert resp["source"] == "local"
        assert len(resp["escalation_id"]) == 16

        events = [
            json.loads(line) for line in
            (run_dir / "session_log.jsonl").read_text().strip().splitlines()
        ]
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "clarification_escalated_to_worker"
        assert ev["run_id"] == "run_e"
        assert ev["payload"]["question"] == "is the migration safe?"
        assert ev["payload"]["language"] == "zh"
        assert ev["payload"]["reason"] == "im_operator"
        assert ev["payload"]["operator"] == "song"
        assert ev["payload"]["confidence"] == 0.2
        assert ev["payload"]["transport"] == "pending_0_3_8"
        assert ev["payload"]["escalation_id"] == resp["escalation_id"]


class TestAppendTimelineEvent:
    def test_writes_event(self, tmp_path):
        from supervisor.operator.api import append_timeline_event
        log_path = tmp_path / "session_log.jsonl"
        append_timeline_event(log_path, "run_x", "test_event", {"key": "val"})
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["run_id"] == "run_x"
        assert record["event_type"] == "test_event"
        assert record["seq"] == 1
        assert record["payload"]["key"] == "val"

    def test_increments_seq(self, tmp_path):
        from supervisor.operator.api import append_timeline_event
        log_path = tmp_path / "session_log.jsonl"
        append_timeline_event(log_path, "run_x", "ev1", {})
        append_timeline_event(log_path, "run_x", "ev2", {})
        lines = log_path.read_text().strip().splitlines()
        assert json.loads(lines[0])["seq"] == 1
        assert json.loads(lines[1])["seq"] == 2
