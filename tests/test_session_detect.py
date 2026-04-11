"""Tests for session detection logic."""
import json

from supervisor.session_detect import detect_cwd_from_jsonl


def test_codex_cwd_from_session_meta(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    jsonl.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": "/home/user/project"}}) + "\n"
    )
    assert detect_cwd_from_jsonl(jsonl, "codex") == "/home/user/project"


def test_codex_cwd_from_turn_context(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    jsonl.write_text(
        json.dumps({"type": "turn_context", "payload": {"cwd": "/tmp/work"}}) + "\n"
    )
    assert detect_cwd_from_jsonl(jsonl, "codex") == "/tmp/work"


def test_missing_cwd_returns_empty(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    jsonl.write_text(json.dumps({"type": "event_msg", "payload": {}}) + "\n")
    assert detect_cwd_from_jsonl(jsonl, "codex") == ""
