"""Tests for the Stop-hook handler (supervisor.hook)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from supervisor import hook


class TestInstructionIO:
    def test_write_and_read_instruction(self, tmp_path):
        hook.write_instruction(
            "sid-1",
            instruction_id="inst-1",
            content="do the thing",
            run_id="run-1",
            node_id="n1",
            root=tmp_path,
        )
        data = hook.read_instruction("sid-1", root=tmp_path)
        assert data is not None
        assert data["instruction_id"] == "inst-1"
        assert data["content"] == "do the thing"
        assert data["content_sha256"] == hook._sha256("do the thing")
        assert data["run_id"] == "run-1"
        assert data["node_id"] == "n1"
        assert data["schema"] == hook.INSTRUCTION_SCHEMA

    def test_read_missing_returns_none(self, tmp_path):
        assert hook.read_instruction("nope", root=tmp_path) is None

    def test_read_malformed_returns_none(self, tmp_path):
        p = hook.instruction_path("sid-1", root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        assert hook.read_instruction("sid-1", root=tmp_path) is None

    def test_read_wrong_schema_returns_none(self, tmp_path):
        p = hook.instruction_path("sid-1", root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": "wrong.v1", "content": "x"}), encoding="utf-8")
        assert hook.read_instruction("sid-1", root=tmp_path) is None

    def test_write_rejects_empty_args(self, tmp_path):
        with pytest.raises(ValueError):
            hook.write_instruction("", instruction_id="i", content="c", root=tmp_path)
        with pytest.raises(ValueError):
            hook.write_instruction("s", instruction_id="", content="c", root=tmp_path)
        with pytest.raises(ValueError):
            hook.write_instruction("s", instruction_id="i", content=None, root=tmp_path)

    def test_write_is_atomic_overwrite(self, tmp_path):
        hook.write_instruction(
            "sid", instruction_id="i1", content="first", root=tmp_path,
        )
        hook.write_instruction(
            "sid", instruction_id="i2", content="second", root=tmp_path,
        )
        data = hook.read_instruction("sid", root=tmp_path)
        assert data["instruction_id"] == "i2"
        assert data["content"] == "second"


class TestRunStopHook:
    def test_no_session_id_exits_zero(self, tmp_path):
        r = hook.run_stop_hook("", root=tmp_path)
        assert r.exit_code == 0
        assert r.stderr == ""

    def test_delivers_pending_instruction(self, tmp_path):
        hook.write_instruction(
            "sid", instruction_id="i1", content="next step", root=tmp_path,
        )
        r = hook.run_stop_hook("sid", root=tmp_path)
        assert r.exit_code == 2
        assert r.stderr == "next step"
        assert r.delivered_instruction_id == "i1"

        ack = hook.read_ack("sid", root=tmp_path)
        assert ack is not None
        assert ack["instruction_id"] == "i1"
        assert ack["content_sha256"] == hook._sha256("next step")
        assert ack["session_id"] == "sid"

    def test_does_not_redeliver_same_instruction(self, tmp_path):
        hook.write_instruction(
            "sid", instruction_id="i1", content="next step", root=tmp_path,
        )
        r1 = hook.run_stop_hook("sid", root=tmp_path)
        assert r1.exit_code == 2
        # Second invocation without a new instruction: no re-delivery.
        r2 = hook.run_stop_hook("sid", root=tmp_path)
        assert r2.delivered_instruction_id == ""
        # No supervisor active → exit 0.
        assert r2.exit_code == 0

    def test_redelivers_when_instruction_id_changes(self, tmp_path):
        hook.write_instruction("sid", instruction_id="i1", content="A", root=tmp_path)
        r1 = hook.run_stop_hook("sid", root=tmp_path)
        assert r1.exit_code == 2 and r1.stderr == "A"

        hook.write_instruction("sid", instruction_id="i2", content="B", root=tmp_path)
        r2 = hook.run_stop_hook("sid", root=tmp_path)
        assert r2.exit_code == 2
        assert r2.stderr == "B"
        assert r2.delivered_instruction_id == "i2"

    def test_falls_back_to_continue_message_when_run_active(self, tmp_path, monkeypatch):
        # No pending instruction; simulate an active run.
        state_file = tmp_path / hook.STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"top_state": "RUNNING"}), encoding="utf-8")
        pid_file = tmp_path / hook.PID_FILE
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        r = hook.run_stop_hook("sid", root=tmp_path)
        assert r.exit_code == 2
        assert "Supervisor run is active" in r.stderr
        assert r.delivered_instruction_id == ""

    def test_terminal_state_means_no_block(self, tmp_path):
        state_file = tmp_path / hook.STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"top_state": "COMPLETED"}), encoding="utf-8")
        pid_file = tmp_path / hook.PID_FILE
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        r = hook.run_stop_hook("sid", root=tmp_path)
        assert r.exit_code == 0

    def test_stale_pid_means_no_block(self, tmp_path):
        state_file = tmp_path / hook.STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"top_state": "RUNNING"}), encoding="utf-8")
        pid_file = tmp_path / hook.PID_FILE
        # PID 1 is init; kill -0 succeeds for init but a user-owned supervisor
        # check should still work. Use a very high PID unlikely to exist.
        pid_file.write_text("999999", encoding="utf-8")

        r = hook.run_stop_hook("sid", root=tmp_path)
        assert r.exit_code == 0

    def test_empty_session_still_blocks_when_run_active(self, tmp_path):
        # Even if the hook can't resolve a session_id, a live supervisor run
        # in the cwd must still block exit so the agent keeps working.
        state_file = tmp_path / hook.STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"top_state": "RUNNING"}), encoding="utf-8")
        (tmp_path / hook.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")

        r = hook.run_stop_hook("", root=tmp_path)
        assert r.exit_code == 2
        assert "Supervisor run is active" in r.stderr

    def test_per_run_state_file_counts_as_active(self, tmp_path):
        # Daemon layout: .supervisor/runtime/runs/<run_id>/state.json
        run_dir = tmp_path / hook.RUNS_DIR / "run-abc"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "state.json").write_text(
            json.dumps({"top_state": "RUNNING"}), encoding="utf-8"
        )
        (tmp_path / hook.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")

        r = hook.run_stop_hook("", root=tmp_path)
        assert r.exit_code == 2
        assert "Supervisor run is active" in r.stderr

    def test_per_run_state_all_terminal_means_no_block(self, tmp_path):
        run_dir = tmp_path / hook.RUNS_DIR / "run-done"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "state.json").write_text(
            json.dumps({"top_state": "COMPLETED"}), encoding="utf-8"
        )
        (tmp_path / hook.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")

        r = hook.run_stop_hook("", root=tmp_path)
        assert r.exit_code == 0

    def test_content_hash_mismatch_refuses_to_deliver(self, tmp_path):
        # Hand-craft a corrupted instruction file whose declared content_sha256
        # disagrees with the actual content. The hook must refuse to ACK it.
        p = hook.instruction_path("sid", root=tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "schema": hook.INSTRUCTION_SCHEMA,
                    "instruction_id": "i1",
                    "content": "real content",
                    "content_sha256": "deadbeef",  # wrong hash
                }
            ),
            encoding="utf-8",
        )
        r = hook.run_stop_hook("sid", root=tmp_path)
        # No delivery, no ACK written.
        assert r.delivered_instruction_id == ""
        assert hook.read_ack("sid", root=tmp_path) is None
        # With no active supervisor, exit 0; otherwise continue message.
        assert r.exit_code == 0
