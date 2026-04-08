"""Tests for the config system."""
from __future__ import annotations

from supervisor.config import RuntimeConfig


def test_defaults():
    cfg = RuntimeConfig()
    assert cfg.judge_model is None
    assert cfg.poll_interval_sec == 2.0
    assert cfg.runtime_dir == ".supervisor/runtime"


def test_from_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "pane_target: codex\n"
        "judge_model: openai/gpt-4o-mini\n"
        "poll_interval_sec: 5.0\n"
        "unknown_key: ignored\n"
    )
    cfg = RuntimeConfig.from_file(str(config_file))
    assert cfg.pane_target == "codex"
    assert cfg.judge_model == "openai/gpt-4o-mini"
    assert cfg.poll_interval_sec == 5.0


def test_from_env(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_PANE_TARGET", "claude")
    monkeypatch.setenv("SUPERVISOR_POLL_INTERVAL_SEC", "3.5")
    monkeypatch.setenv("SUPERVISOR_JUDGE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    cfg = RuntimeConfig.from_env()
    assert cfg.pane_target == "claude"
    assert cfg.poll_interval_sec == 3.5
    assert cfg.judge_model == "anthropic/claude-haiku-4-5-20251001"


def test_load_priority(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("pane_target: from_file\npoll_interval_sec: 1.0\n")
    monkeypatch.setenv("SUPERVISOR_PANE_TARGET", "from_env")

    cfg = RuntimeConfig.load(str(config_file))
    # env overrides file
    assert cfg.pane_target == "from_env"
    # file value preserved when no env override
    assert cfg.poll_interval_sec == 1.0


def test_default_config_yaml():
    cfg = RuntimeConfig()
    yaml_str = cfg.default_config_yaml()
    assert "pane_target" in yaml_str
    assert "judge_model" in yaml_str
    assert "null" in yaml_str
