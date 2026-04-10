"""Tests for OpenRelaySurface (mocked oly CLI)."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from supervisor.adapters.open_relay_surface import OpenRelaySurface, OpenRelaySurfaceError
from supervisor.adapters.session_adapter import SessionAdapter
from supervisor.adapters.surface_factory import create_surface


def _mock_run(stdout="", returncode=0):
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


class TestOpenRelaySurfaceRead:
    @patch("subprocess.run")
    def test_read_calls_oly_logs(self, mock_run):
        mock_run.return_value = _mock_run(stdout="some output\n")
        surface = OpenRelaySurface("sess-123")
        text = surface.read(lines=50)
        assert text == "some output\n"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "oly"
        assert "logs" in cmd
        assert "sess-123" in cmd
        assert "50" in cmd


class TestOpenRelaySurfaceInject:
    @patch("subprocess.run")
    def test_inject_calls_oly_send(self, mock_run):
        mock_run.return_value = _mock_run()
        surface = OpenRelaySurface("sess-123")
        surface.inject("hello world")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "oly"
        assert "send" in cmd
        assert "sess-123" in cmd
        assert "hello world" in cmd
        assert "key:enter" in cmd


class TestOpenRelaySurfaceCwd:
    @patch("subprocess.run")
    def test_cwd_from_session_metadata(self, mock_run):
        sessions = [{"id": "sess-123", "cwd": "/home/user/project"}]
        mock_run.return_value = _mock_run(stdout=json.dumps(sessions))
        surface = OpenRelaySurface("sess-123")
        assert surface.current_cwd() == "/home/user/project"

    @patch("subprocess.run")
    def test_cwd_returns_empty_on_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        surface = OpenRelaySurface("sess-123")
        assert surface.current_cwd() == ""


class TestOpenRelaySurfaceDoctor:
    @patch("shutil.which", return_value="/usr/bin/oly")
    @patch("subprocess.run")
    def test_doctor_healthy(self, mock_run, mock_which):
        sessions = [{"id": "sess-123", "status": "running"}]
        mock_run.return_value = _mock_run(stdout=json.dumps(sessions))
        surface = OpenRelaySurface("sess-123")
        info = surface.doctor()
        assert info["ok"] is True

    @patch("shutil.which", return_value=None)
    def test_doctor_no_oly(self, mock_which):
        surface = OpenRelaySurface("sess-123")
        info = surface.doctor()
        assert info["ok"] is False
        assert any("oly" in i for i in info["issues"])

    @patch("shutil.which", return_value="/usr/bin/oly")
    @patch("subprocess.run")
    def test_doctor_session_not_found(self, mock_run, mock_which):
        mock_run.return_value = _mock_run(stdout="[]")
        surface = OpenRelaySurface("nonexistent")
        info = surface.doctor()
        assert info["ok"] is False
        assert any("not found" in i for i in info["issues"])


class TestSessionAdapterProtocol:
    def test_open_relay_has_all_protocol_methods(self):
        methods = ["read", "inject", "current_cwd", "session_id", "doctor"]
        for m in methods:
            assert hasattr(OpenRelaySurface, m), f"OpenRelaySurface missing {m}"

    def test_open_relay_instance_check(self):
        surface = OpenRelaySurface("test-123")
        assert isinstance(surface, SessionAdapter)


class TestSurfaceFactory:
    def test_create_tmux(self):
        surface = create_surface("tmux", "%0")
        assert hasattr(surface, "read")
        assert hasattr(surface, "inject")

    def test_create_open_relay(self):
        surface = create_surface("open_relay", "sess-123")
        assert isinstance(surface, OpenRelaySurface)
        assert surface.session_id() == "sess-123"

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="unknown surface type"):
            create_surface("magic", "target")
