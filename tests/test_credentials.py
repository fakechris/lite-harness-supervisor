"""Tests for credential resolution and persistence."""
from __future__ import annotations

import yaml

from supervisor.config import RuntimeConfig
from supervisor.credentials import resolve_credentials, persist_credential


def test_resolve_missing_defaults():
    """Fresh config returns expected missing credentials."""
    config = RuntimeConfig()
    missing = resolve_credentials(config)
    keys = {m.key for m in missing}
    assert "worker_provider" in keys
    assert "worker_model" in keys


def test_resolve_all_set():
    """Fully populated config returns no missing credentials."""
    config = RuntimeConfig(
        worker_provider="anthropic",
        worker_model="claude-opus-4-6",
        judge_model="anthropic/haiku",
    )
    missing = resolve_credentials(config)
    assert len(missing) == 0


def test_persist_global(tmp_path, monkeypatch):
    """persist_credential with scope=global writes to global config."""
    gpath = tmp_path / "defaults.yaml"
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_CONFIG", str(gpath))

    persist_credential("worker_model", "test-model", scope="global")
    assert gpath.exists()

    data = yaml.safe_load(gpath.read_text())
    assert data["worker_model"] == "test-model"


def test_persist_project(tmp_path):
    """persist_credential with scope=project writes to project config."""
    persist_credential("poll_interval_sec", 5.0, scope="project", project_dir=str(tmp_path))
    ppath = tmp_path / ".supervisor" / "config.yaml"
    assert ppath.exists()

    data = yaml.safe_load(ppath.read_text())
    assert data["poll_interval_sec"] == 5.0
