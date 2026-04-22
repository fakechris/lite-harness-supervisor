"""Tests for supervisor.hook_install — Claude Code / Codex hook wiring."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor import hook_install


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class TestInstallStopHook:
    def test_creates_file_when_missing(self, tmp_path):
        target = tmp_path / "settings.json"
        changed, msg = hook_install.install_stop_hook(target)
        assert changed is True
        assert "installed" in msg.lower()

        data = json.loads(target.read_text())
        stop = data["hooks"]["Stop"]
        assert len(stop) == 1
        assert stop[0]["hooks"][0]["command"] == hook_install.DEFAULT_COMMAND
        assert stop[0]["hooks"][0]["type"] == "command"

    def test_idempotent_second_call_is_noop(self, tmp_path):
        target = tmp_path / "settings.json"
        hook_install.install_stop_hook(target)
        changed, msg = hook_install.install_stop_hook(target)
        assert changed is False
        assert "already installed" in msg

    def test_preserves_other_hook_groups(self, tmp_path):
        target = tmp_path / "settings.json"
        _write_json(target, {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "other-tool"}]}
                ],
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "x"}]}
                ],
            },
            "model": "opus",
        })
        changed, _ = hook_install.install_stop_hook(target)
        assert changed is True

        data = json.loads(target.read_text())
        stop = data["hooks"]["Stop"]
        commands = [h["command"] for g in stop for h in g["hooks"]]
        assert "other-tool" in commands
        assert hook_install.DEFAULT_COMMAND in commands
        # Unrelated settings preserved.
        assert data["model"] == "opus"
        assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "x"

    def test_refuses_when_hooks_is_wrong_type(self, tmp_path):
        target = tmp_path / "settings.json"
        _write_json(target, {"hooks": "not-an-object"})
        changed, msg = hook_install.install_stop_hook(target)
        assert changed is False
        assert "not an object" in msg

    def test_refuses_when_stop_is_wrong_type(self, tmp_path):
        target = tmp_path / "settings.json"
        _write_json(target, {"hooks": {"Stop": {"oops": "dict"}}})
        changed, msg = hook_install.install_stop_hook(target)
        assert changed is False
        assert "not a list" in msg


class TestUninstallStopHook:
    def test_removes_our_entry_only(self, tmp_path):
        target = tmp_path / "settings.json"
        # Install alongside an existing foreign entry.
        _write_json(target, {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "other-tool"}]}
                ],
            },
        })
        hook_install.install_stop_hook(target)

        changed, _ = hook_install.uninstall_stop_hook(target)
        assert changed is True
        data = json.loads(target.read_text())
        stop = data["hooks"]["Stop"]
        commands = [h["command"] for g in stop for h in g["hooks"]]
        assert commands == ["other-tool"]

    def test_cleans_empty_sections(self, tmp_path):
        target = tmp_path / "settings.json"
        hook_install.install_stop_hook(target)
        hook_install.uninstall_stop_hook(target)
        data = json.loads(target.read_text())
        # With no other hooks or keys, the whole `hooks` block should be gone.
        assert "hooks" not in data

    def test_missing_file_is_noop(self, tmp_path):
        target = tmp_path / "does-not-exist.json"
        changed, msg = hook_install.uninstall_stop_hook(target)
        assert changed is False
        assert "no settings file" in msg

    def test_entry_not_present_is_noop(self, tmp_path):
        target = tmp_path / "settings.json"
        _write_json(target, {
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}
        })
        changed, msg = hook_install.uninstall_stop_hook(target)
        assert changed is False
        assert "not present" in msg


class TestResolveTargets:
    def test_only_returns_agents_with_home_dir(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        # No .codex dir — should be skipped.
        targets = hook_install.resolve_targets("both", home=tmp_path)
        labels = [t.label for t in targets]
        assert labels == ["Claude Code"]

    def test_returns_both_when_both_exist(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".codex").mkdir()
        targets = hook_install.resolve_targets("both", home=tmp_path)
        assert [t.label for t in targets] == ["Claude Code", "Codex"]
        assert targets[0].settings_path == tmp_path / ".claude" / "settings.json"
        assert targets[1].settings_path == tmp_path / ".codex" / "hooks.json"

    def test_single_agent_filter(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".codex").mkdir()
        assert [t.label for t in hook_install.resolve_targets("claude", home=tmp_path)] == ["Claude Code"]
        assert [t.label for t in hook_install.resolve_targets("codex", home=tmp_path)] == ["Codex"]
