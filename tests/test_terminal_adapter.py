"""Tests for the terminal adapter (mocked tmux subprocess calls)."""
from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from supervisor.terminal.adapter import (
    TerminalAdapter,
    TerminalAdapterError,
    ReadGuardError,
    PaneInfo,
)


def _mock_run(stdout="", returncode=0, **kwargs):
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


class TestReadGuard:
    """Read-before-act guard prevents blind interaction."""

    @patch("subprocess.run")
    def test_type_without_read_raises(self, mock_run):
        adapter = TerminalAdapter("%0")
        with pytest.raises(ReadGuardError, match="must read pane"):
            adapter.type_text("hello")

    @patch("subprocess.run")
    def test_send_keys_without_read_raises(self, mock_run):
        adapter = TerminalAdapter("%0")
        with pytest.raises(ReadGuardError, match="must read pane"):
            adapter.send_keys("Enter")

    @patch("subprocess.run")
    def test_read_then_type_succeeds(self, mock_run):
        mock_run.return_value = _mock_run(stdout="some output\n")
        adapter = TerminalAdapter("%0")
        adapter.read()
        adapter.type_text("hello")
        # Calls: socket detect + capture-pane + send-keys = 3 (may vary by env)
        assert mock_run.call_count >= 2

    @patch("subprocess.run")
    def test_type_clears_guard(self, mock_run):
        mock_run.return_value = _mock_run(stdout="output\n")
        adapter = TerminalAdapter("%0")
        adapter.read()
        adapter.type_text("hello")
        with pytest.raises(ReadGuardError):
            adapter.type_text("again")

    @patch("subprocess.run")
    def test_send_keys_clears_guard(self, mock_run):
        mock_run.return_value = _mock_run(stdout="output\n")
        adapter = TerminalAdapter("%0")
        adapter.read()
        adapter.send_keys("Enter")
        with pytest.raises(ReadGuardError):
            adapter.send_keys("Enter")


class TestTargetResolution:
    """Pane target resolution: %id, session:win, label."""

    @patch("subprocess.run")
    def test_pane_id_direct(self, mock_run):
        mock_run.return_value = _mock_run(stdout="text\n")
        adapter = TerminalAdapter("%42")
        adapter.read()
        # Should pass %42 directly to tmux
        args = mock_run.call_args[0][0]
        assert "%42" in args

    @patch("subprocess.run")
    def test_session_window_direct(self, mock_run):
        mock_run.return_value = _mock_run(stdout="text\n")
        adapter = TerminalAdapter("main:0")
        adapter.read()
        args = mock_run.call_args[0][0]
        assert "main:0" in args

    @patch("subprocess.run")
    def test_label_resolved(self, mock_run):
        def side_effect(cmd, **kwargs):
            if "list-panes" in cmd:
                return _mock_run(stdout="%5 codex\n%6 claude\n")
            return _mock_run(stdout="pane text\n")

        mock_run.side_effect = side_effect
        adapter = TerminalAdapter("codex")
        text = adapter.read()
        assert text == "pane text\n"

    @patch("subprocess.run")
    def test_label_not_found_raises(self, mock_run):
        mock_run.return_value = _mock_run(stdout="%5 other\n")
        adapter = TerminalAdapter("nonexistent")
        with pytest.raises(TerminalAdapterError, match="no pane found"):
            adapter.read()


class TestInject:
    """inject() combines type_text + Enter in one guarded call."""

    @patch("subprocess.run")
    def test_inject_succeeds_after_read(self, mock_run):
        mock_run.return_value = _mock_run(stdout="output\n")
        adapter = TerminalAdapter("%0")
        adapter.read()
        adapter.inject("hello world")
        # Calls: socket detect + capture-pane + send-keys text + send-keys Enter
        assert mock_run.call_count >= 3

    @patch("subprocess.run")
    def test_inject_without_read_raises(self, mock_run):
        adapter = TerminalAdapter("%0")
        with pytest.raises(ReadGuardError):
            adapter.inject("hello")

    @patch("subprocess.run")
    def test_inject_clears_guard(self, mock_run):
        mock_run.return_value = _mock_run(stdout="output\n")
        adapter = TerminalAdapter("%0")
        adapter.read()
        adapter.inject("hello")
        with pytest.raises(ReadGuardError):
            adapter.inject("again")


class TestInjectionReadiness:

    @patch("time.sleep", return_value=None)
    @patch("subprocess.run")
    def test_injection_readiness_defers_when_buffer_is_still_changing(self, mock_run, _mock_sleep):
        def side_effect(cmd, **kwargs):
            if "display-message" in cmd:
                return _mock_run(stdout="1 0 0 29 30\n")
            if "capture-pane" in cmd:
                count = getattr(side_effect, "captures", 0)
                side_effect.captures = count + 1
                if count == 0:
                    return _mock_run(stdout="buffer snapshot one\n")
                return _mock_run(stdout="buffer snapshot two\n")
            return _mock_run(stdout="")

        mock_run.side_effect = side_effect
        adapter = TerminalAdapter("%0", tmux_socket="/tmp/test.sock")
        adapter._pane_id = "%0"

        outcome, reason = adapter.injection_readiness()

        assert outcome == "defer"
        assert reason == "buffer_changed"

    def test_cursor_typing_status_treats_idle_codex_prompt_as_not_busy(self):
        status = TerminalAdapter._cursor_typing_status(
            "› \n",
            {"active": True, "dead": False, "cursor_x": 2, "cursor_y": 0, "height": 1},
        )
        assert status == "not_busy"

    def test_cursor_typing_status_treats_prompt_with_user_input_as_busy(self):
        status = TerminalAdapter._cursor_typing_status(
            "› hello\n",
            {"active": True, "dead": False, "cursor_x": 6, "cursor_y": 0, "height": 1},
        )
        assert status == "busy"


class TestListPanes:

    @patch("subprocess.run")
    def test_list_panes_parses(self, mock_run):
        mock_run.return_value = _mock_run(
            stdout="%0\tmain:0\t120x30\tbash\twork\t/home/user\n"
                   "%1\tmain:0\t120x30\tnode\tcodex\t/home/user/project\n"
        )
        adapter = TerminalAdapter("%0")
        panes = adapter.list_panes()
        assert len(panes) == 2
        assert panes[0].pane_id == "%0"
        assert panes[0].label == "work"
        assert panes[1].label == "codex"
        assert panes[1].process == "node"


class TestDoctor:

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/tmux")
    def test_doctor_ok(self, mock_which, mock_run):
        mock_run.return_value = _mock_run(
            stdout="%0\tmain:0\t120x30\tbash\t\t/home\n"
        )
        adapter = TerminalAdapter("%0")
        info = adapter.doctor()
        assert info["ok"] is True
        assert info["pane_count"] == 1

    @patch("subprocess.run")
    @patch("shutil.which", return_value=None)
    def test_doctor_no_tmux(self, mock_which, mock_run):
        mock_run.side_effect = FileNotFoundError("tmux")
        adapter = TerminalAdapter("%0")
        info = adapter.doctor()
        assert info["ok"] is False
        assert any("tmux" in i for i in info["issues"])


class TestTmuxErrors:

    @patch("subprocess.run")
    def test_tmux_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        adapter = TerminalAdapter("%0")
        with pytest.raises(TerminalAdapterError, match="not found"):
            adapter.read()

    @patch("subprocess.run")
    def test_tmux_nonzero_exit(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1)
        mock_run.return_value.stderr = "session not found"
        adapter = TerminalAdapter("%0")
        with pytest.raises(TerminalAdapterError, match="failed"):
            adapter.read()
