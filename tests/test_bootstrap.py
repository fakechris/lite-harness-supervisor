"""Tests for the zero-setup bootstrap API."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from supervisor.bootstrap import bootstrap, BootstrapResult


def _step_by_name(result: BootstrapResult, name: str) -> dict | None:
    for step in result.steps:
        if step["name"] == name:
            return step
    return None


def test_bootstrap_no_tmux(tmp_path, monkeypatch):
    """Without $TMUX, bootstrap fails at step 1."""
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(tmp_path / "g.yaml"))
    result = bootstrap(cwd=str(tmp_path))
    assert not result.ok
    assert "tmux" in result.error.lower()
    tmux_step = _step_by_name(result, "tmux_check")
    assert tmux_step["status"] == "failed"


def test_bootstrap_fresh_project(tmp_path, monkeypatch):
    """Fresh project with no .supervisor/ gets auto-initialized."""
    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1234,0")
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(tmp_path / "g.yaml"))

    with patch("supervisor.bootstrap._ensure_daemon_running"):
        result = bootstrap(cwd=str(tmp_path))

    assert result.ok, f"error: {result.error}"
    assert (tmp_path / ".supervisor").exists()
    assert (tmp_path / ".supervisor" / "config.yaml").exists()
    init_step = _step_by_name(result, "init_repair")
    assert init_step["status"] == "ok"


def test_bootstrap_existing_project(tmp_path, monkeypatch):
    """Existing project skips init."""
    (tmp_path / ".supervisor").mkdir()
    (tmp_path / ".supervisor" / "config.yaml").write_text("surface_type: tmux\n")
    (tmp_path / ".supervisor" / "runtime").mkdir(parents=True)

    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1234,0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(tmp_path / "g.yaml"))

    with patch("supervisor.bootstrap._ensure_daemon_running"):
        result = bootstrap(cwd=str(tmp_path))

    assert result.ok
    init_step = _step_by_name(result, "init_repair")
    assert init_step["status"] == "skipped"


def test_bootstrap_daemon_already_running(tmp_path, monkeypatch):
    """If daemon is running, skip auto-start."""
    (tmp_path / ".supervisor").mkdir()
    (tmp_path / ".supervisor" / "config.yaml").write_text("surface_type: tmux\n")

    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1234,0")
    monkeypatch.setenv("TMUX_PANE", "%2")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(tmp_path / "g.yaml"))

    mock_client = MagicMock()
    mock_client.is_running.return_value = True

    with patch("supervisor.daemon.client.DaemonClient", return_value=mock_client):
        result = bootstrap(cwd=str(tmp_path))

    assert result.ok
    daemon_step = _step_by_name(result, "daemon_ensure")
    assert daemon_step["status"] == "ok"


def test_bootstrap_pane_detect(tmp_path, monkeypatch):
    """Pane target comes from $TMUX_PANE."""
    (tmp_path / ".supervisor").mkdir()
    (tmp_path / ".supervisor" / "config.yaml").write_text("surface_type: tmux\n")

    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1234,0")
    monkeypatch.setenv("TMUX_PANE", "%42")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(tmp_path / "g.yaml"))

    with patch("supervisor.bootstrap._ensure_daemon_running"):
        result = bootstrap(cwd=str(tmp_path))

    assert result.ok
    assert result.pane_target == "%42"
    assert result.surface_type == "tmux"


def test_bootstrap_no_pane(tmp_path, monkeypatch):
    """Without $TMUX_PANE, bootstrap fails at pane detection."""
    (tmp_path / ".supervisor").mkdir()
    (tmp_path / ".supervisor" / "config.yaml").write_text("surface_type: tmux\n")

    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1234,0")
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(tmp_path / "g.yaml"))

    with patch("supervisor.bootstrap._ensure_daemon_running"):
        result = bootstrap(cwd=str(tmp_path))

    assert not result.ok
    assert "pane" in result.error.lower()


def test_bootstrap_result_structure(tmp_path, monkeypatch):
    """Bootstrap result has all expected fields."""
    (tmp_path / ".supervisor").mkdir()
    (tmp_path / ".supervisor" / "config.yaml").write_text("surface_type: tmux\n")

    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1234,0")
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(tmp_path / "g.yaml"))

    with patch("supervisor.bootstrap._ensure_daemon_running"):
        result = bootstrap(cwd=str(tmp_path))

    assert isinstance(result.steps, list)
    assert len(result.steps) >= 5
    step_names = [s["name"] for s in result.steps]
    assert "tmux_check" in step_names
    assert "init_repair" in step_names
    assert "config_load" in step_names
    assert "daemon_ensure" in step_names
    assert "pane_detect" in step_names
