"""Tests for session detection logic."""
import json
from pathlib import Path

from supervisor import session_detect
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


def test_detect_session_id_prefers_codex_thread_id(monkeypatch):
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-123")

    assert session_detect.detect_session_id("codex") == "thread-123"


def test_find_jsonl_for_session_matches_exact_codex_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    sessions_dir = tmp_path / ".codex" / "sessions" / "2026" / "04" / "11"
    sessions_dir.mkdir(parents=True)
    exact = sessions_dir / "rollout-2026-04-11T12-00-00-thread-123.jsonl"
    exact.write_text("")
    newer = sessions_dir / "rollout-2026-04-11T12-05-00-thread-999.jsonl"
    newer.write_text("")

    assert session_detect.find_jsonl_for_session("thread-123", "codex") == exact
