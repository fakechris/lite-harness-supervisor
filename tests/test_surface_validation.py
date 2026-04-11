"""Tests for target format validation and cwd fallback."""
from __future__ import annotations

from unittest.mock import MagicMock
import argparse

from supervisor.adapters.surface_factory import create_surface
from supervisor.adapters.jsonl_observer import JsonlObserver


class TestSurfaceFactory:
    def test_tmux_creates_terminal_adapter(self):
        surface = create_surface("tmux", "%0")
        assert hasattr(surface, "read")
        assert hasattr(surface, "inject")

    def test_jsonl_creates_observer(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text("")
        surface = create_surface("jsonl", str(jsonl))
        assert isinstance(surface, JsonlObserver)
        assert surface.is_observation_only is True

    def test_open_relay_creates_surface(self):
        surface = create_surface("open_relay", "test-session")
        assert hasattr(surface, "read")

    def test_unknown_type_raises(self):
        import pytest
        with pytest.raises(ValueError, match="unknown surface type"):
            create_surface("magic", "target")


class TestJsonlObservationOnly:
    def test_is_observation_only(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text("")
        obs = JsonlObserver(str(jsonl))
        assert obs.is_observation_only is True

    def test_tmux_is_not_observation_only(self):
        from supervisor.terminal.adapter import TerminalAdapter
        adapter = TerminalAdapter("%0")
        assert not getattr(adapter, "is_observation_only", False)


class TestCwdFallback:
    def test_get_cwd_uses_terminal_first(self):
        from supervisor.loop import SupervisorLoop
        from supervisor.storage.state_store import StateStore
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(td)
            loop = SupervisorLoop(store)

            terminal = MagicMock()
            terminal.current_cwd.return_value = "/from/terminal"
            state = MagicMock()
            state.workspace_root = "/from/state"

            assert loop._get_cwd(terminal, state) == "/from/terminal"

    def test_get_cwd_falls_back_to_workspace(self):
        from supervisor.loop import SupervisorLoop
        from supervisor.storage.state_store import StateStore
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(td)
            loop = SupervisorLoop(store)

            terminal = MagicMock()
            terminal.current_cwd.return_value = ""  # empty!
            state = MagicMock()
            state.workspace_root = "/fallback/path"

            assert loop._get_cwd(terminal, state) == "/fallback/path"

    def test_get_cwd_returns_none_when_both_empty(self):
        from supervisor.loop import SupervisorLoop
        from supervisor.storage.state_store import StateStore
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(td)
            loop = SupervisorLoop(store)

            terminal = MagicMock()
            terminal.current_cwd.return_value = ""
            state = MagicMock()
            state.workspace_root = ""

            assert loop._get_cwd(terminal, state) is None
