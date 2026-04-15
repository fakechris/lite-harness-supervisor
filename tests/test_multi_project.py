"""Tests for multi-project config sharing and isolation."""
from __future__ import annotations

import yaml
from unittest.mock import patch

from supervisor.config import RuntimeConfig, save_global_config, save_project_config
from supervisor.credentials import persist_credential, resolve_credentials
from supervisor.bootstrap import bootstrap


def test_two_projects_shared_global_config(tmp_path, monkeypatch):
    """Two projects share global config for inheritable fields."""
    gpath = tmp_path / "global.yaml"
    gpath.write_text("worker_model: shared-model\n")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))

    proj_a = tmp_path / "project-a"
    proj_b = tmp_path / "project-b"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_a / ".supervisor").mkdir()
    (proj_a / ".supervisor" / "config.yaml").write_text("surface_type: tmux\n")
    (proj_b / ".supervisor").mkdir()
    (proj_b / ".supervisor" / "config.yaml").write_text("surface_type: tmux\n")

    cfg_a = RuntimeConfig.load(str(proj_a / ".supervisor" / "config.yaml"))
    cfg_b = RuntimeConfig.load(str(proj_b / ".supervisor" / "config.yaml"))

    assert cfg_a.worker_model == "shared-model"
    assert cfg_b.worker_model == "shared-model"


def test_project_local_override(tmp_path, monkeypatch):
    """Project A overrides global, project B uses global."""
    gpath = tmp_path / "global.yaml"
    gpath.write_text("worker_model: global-model\n")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))

    proj_a = tmp_path / "project-a"
    proj_b = tmp_path / "project-b"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_a / ".supervisor").mkdir()
    (proj_a / ".supervisor" / "config.yaml").write_text("worker_model: local-model-a\n")
    (proj_b / ".supervisor").mkdir()
    (proj_b / ".supervisor" / "config.yaml").write_text("surface_type: tmux\n")

    cfg_a = RuntimeConfig.load(str(proj_a / ".supervisor" / "config.yaml"))
    cfg_b = RuntimeConfig.load(str(proj_b / ".supervisor" / "config.yaml"))

    assert cfg_a.worker_model == "local-model-a"
    assert cfg_b.worker_model == "global-model"


def test_global_credential_reuse(tmp_path, monkeypatch):
    """Credential saved globally from project A is visible to project B."""
    gpath = tmp_path / "global.yaml"
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))

    persist_credential("worker_model", "shared-via-cred", scope="global")

    proj_b = tmp_path / "project-b"
    proj_b.mkdir()
    (proj_b / ".supervisor").mkdir()
    (proj_b / ".supervisor" / "config.yaml").write_text("surface_type: tmux\n")

    cfg_b = RuntimeConfig.load(str(proj_b / ".supervisor" / "config.yaml"))
    assert cfg_b.worker_model == "shared-via-cred"


def test_bootstrap_two_projects(tmp_path, monkeypatch):
    """Bootstrap in two dirs gets independent state but shared config."""
    gpath = tmp_path / "global.yaml"
    gpath.write_text("worker_model: shared\n")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))
    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1234,0")
    monkeypatch.setenv("TMUX_PANE", "%0")

    proj_a = tmp_path / "project-a"
    proj_b = tmp_path / "project-b"
    proj_a.mkdir()
    proj_b.mkdir()

    with patch("supervisor.bootstrap._ensure_daemon_running"), \
         patch("supervisor.bootstrap._validate_pane", return_value=None):
        result_a = bootstrap(cwd=str(proj_a))
        result_b = bootstrap(cwd=str(proj_b))

    assert result_a.ok
    assert result_b.ok
    # Both got their own .supervisor/
    assert (proj_a / ".supervisor").exists()
    assert (proj_b / ".supervisor").exists()
    # Both resolved shared global config
    assert result_a.config.worker_model == "shared"
    assert result_b.config.worker_model == "shared"
