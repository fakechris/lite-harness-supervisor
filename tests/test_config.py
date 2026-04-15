"""Tests for the config system."""
from __future__ import annotations

import yaml

from supervisor.config import (
    RuntimeConfig, global_config_path,
    save_global_config, save_project_config,
)


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
    assert "surface_type" in yaml_str
    assert "surface_target" in yaml_str
    assert "judge_model" in yaml_str
    assert "null" in yaml_str


# ---------------------------------------------------------------------------
# Global config layering
# ---------------------------------------------------------------------------

def test_global_config_inheritable_fields(tmp_path, monkeypatch):
    """Global config sets inheritable field, project doesn't → inherited."""
    gpath = tmp_path / "global.yaml"
    gpath.write_text("worker_model: claude-opus-4-6\njudge_model: anthropic/haiku\n")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))

    cfg = RuntimeConfig.load(None)
    assert cfg.worker_model == "claude-opus-4-6"
    assert cfg.judge_model == "anthropic/haiku"


def test_project_overrides_global(tmp_path, monkeypatch):
    """Project config overrides global for same field."""
    gpath = tmp_path / "global.yaml"
    gpath.write_text("worker_model: global-model\n")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))

    ppath = tmp_path / "project.yaml"
    ppath.write_text("worker_model: project-model\n")

    cfg = RuntimeConfig.load(str(ppath))
    assert cfg.worker_model == "project-model"


def test_global_ignores_project_only_fields(tmp_path, monkeypatch):
    """Global config cannot set project-only fields like surface_target."""
    gpath = tmp_path / "global.yaml"
    gpath.write_text("surface_target: should-be-ignored\nworker_model: inherited\n")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))

    cfg = RuntimeConfig.load(None)
    assert cfg.surface_target == ""  # default, not from global
    assert cfg.worker_model == "inherited"


def test_env_overrides_global_and_project(tmp_path, monkeypatch):
    """Env vars override both global and project."""
    gpath = tmp_path / "global.yaml"
    gpath.write_text("worker_model: from-global\n")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))

    ppath = tmp_path / "project.yaml"
    ppath.write_text("worker_model: from-project\n")
    monkeypatch.setenv("SUPERVISOR_WORKER_MODEL", "from-env")

    cfg = RuntimeConfig.load(str(ppath))
    assert cfg.worker_model == "from-env"


def test_save_global_config_roundtrip(tmp_path, monkeypatch):
    """save_global_config writes and subsequent load reads it."""
    gpath = tmp_path / "defaults.yaml"
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))

    save_global_config("worker_model", "test-model")
    assert gpath.exists()

    data = yaml.safe_load(gpath.read_text())
    assert data["worker_model"] == "test-model"

    # Load picks it up
    cfg = RuntimeConfig.load(None)
    assert cfg.worker_model == "test-model"


def test_save_project_config_roundtrip(tmp_path):
    """save_project_config writes to project .supervisor/config.yaml."""
    save_project_config("poll_interval_sec", 5.0, project_dir=str(tmp_path))
    ppath = tmp_path / ".supervisor" / "config.yaml"
    assert ppath.exists()

    data = yaml.safe_load(ppath.read_text())
    assert data["poll_interval_sec"] == 5.0


def test_global_config_path_env_override(tmp_path, monkeypatch):
    """THIN_SUPERVISOR_GLOBAL_CONFIG overrides default path."""
    custom = tmp_path / "custom.yaml"
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(custom))
    assert global_config_path() == custom
