"""Tests for JSONL transcript observer."""
import json
from pathlib import Path

import pytest

from supervisor.adapters.jsonl_observer import JsonlObserver, JsonlObserverError
from supervisor.adapters.surface_factory import create_surface


class TestJsonlObserverRead:
    def test_reads_new_content(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text(
            json.dumps({"type": "event_msg", "payload": {"content": "hello world"}}) + "\n"
        )
        obs = JsonlObserver(str(jsonl))
        text = obs.read()
        assert "hello world" in text

    def test_incremental_read(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text(
            json.dumps({"type": "event_msg", "payload": {"content": "first"}}) + "\n"
        )
        obs = JsonlObserver(str(jsonl))
        obs.read()  # consume first

        # Append new content
        with jsonl.open("a") as f:
            f.write(json.dumps({"type": "event_msg", "payload": {"content": "second"}}) + "\n")

        text = obs.read()
        assert "second" in text
        # Buffer includes historical text for cross-event checkpoint matching
        # "first" may still be present — that's by design

    def test_empty_file(self, tmp_path):
        jsonl = tmp_path / "empty.jsonl"
        jsonl.write_text("")
        obs = JsonlObserver(str(jsonl))
        assert obs.read() == ""

    def test_missing_file(self, tmp_path):
        obs = JsonlObserver(str(tmp_path / "nonexistent.jsonl"))
        assert obs.read() == ""

    def test_consume_checkpoint_clears_processed_buffer(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        checkpoint = (
            "<checkpoint>\n"
            "status: step_done\n"
            "current_node: step1\n"
            "summary: wrote tests\n"
            "</checkpoint>"
        )
        jsonl.write_text(
            json.dumps({"type": "event_msg", "payload": {"content": checkpoint}}) + "\n"
        )
        obs = JsonlObserver(str(jsonl))
        first = obs.read()
        assert "<checkpoint>" in first

        obs.consume_checkpoint()
        with jsonl.open("a") as f:
            f.write(json.dumps({"type": "event_msg", "payload": {"content": "follow-up"}}) + "\n")

        second = obs.read()
        assert "follow-up" in second
        assert "<checkpoint>" not in second


class TestJsonlObserverCheckpoint:
    def test_checkpoint_in_tool_result(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        checkpoint_text = (
            "<checkpoint>\n"
            "status: step_done\n"
            "current_node: step1\n"
            "summary: wrote tests\n"
            "</checkpoint>"
        )
        jsonl.write_text(
            json.dumps({"type": "tool_result", "payload": {"content": checkpoint_text}}) + "\n"
        )
        obs = JsonlObserver(str(jsonl))
        text = obs.read()
        assert "<checkpoint>" in text
        assert "step_done" in text


class TestJsonlObserverCwd:
    def test_cwd_from_session_meta(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text(
            json.dumps({"type": "session_meta", "payload": {"cwd": "/home/user/project"}}) + "\n"
        )
        obs = JsonlObserver(str(jsonl))
        obs.read()  # trigger parsing
        assert obs.current_cwd() == "/home/user/project"

    def test_cwd_override(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text("")
        obs = JsonlObserver(str(jsonl), cwd="/override")
        assert obs.current_cwd() == "/override"


class TestJsonlObserverDoctor:
    def test_healthy(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text("data\n")
        obs = JsonlObserver(str(jsonl))
        info = obs.doctor()
        assert info["ok"] is True

    def test_missing_file(self, tmp_path):
        obs = JsonlObserver(str(tmp_path / "missing.jsonl"))
        info = obs.doctor()
        assert info["ok"] is False

    def test_empty_session_id_rejected(self):
        with pytest.raises(JsonlObserverError):
            JsonlObserver("")


class TestJsonlObserverInject:
    def test_inject_writes_instruction_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text("")
        obs = JsonlObserver(str(jsonl), session_id_override="test-sess")
        obs.inject("do the next step")
        inst_path = tmp_path / ".supervisor" / "runtime" / "instructions" / "test-sess.json"
        data = json.loads(inst_path.read_text())
        assert data["content"] == "do the next step"
        assert data["schema"] == "instruction.v1"
        assert data["instruction_id"].startswith("legacy-")

    def test_inject_with_id_and_poll_delivery(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text("")
        obs = JsonlObserver(str(jsonl), session_id_override="sid-1")
        obs.inject_with_id("next step", instruction_id="i1", run_id="r1", node_id="n1")

        # No ACK yet.
        assert obs.poll_delivery("i1") is False

        # Simulate the Stop hook by running the real hook handler.
        from supervisor import hook
        result = hook.run_stop_hook("sid-1")
        assert result.exit_code == 2
        assert result.stderr == "next step"

        assert obs.poll_delivery("i1") is True
        # Mismatched instruction_id returns False (ACK is for i1).
        assert obs.poll_delivery("i2") is False


class TestSurfaceFactory:
    def test_create_jsonl(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text("")
        surface = create_surface("jsonl", str(jsonl))
        assert isinstance(surface, JsonlObserver)
