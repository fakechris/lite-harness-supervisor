"""Scenario contract tests for operator command channels.

Verifies:
1. OperatorChannelHost extraction — NotificationManager has no command channel logic
2. Cross-process singleton — advisory file locking prevents double-start
3. CommandChannel protocol — both adapters conform
4. Dispatch-only routing — all channels use dispatch_command()
"""
from __future__ import annotations

import ast
import fcntl
import inspect
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from supervisor.operator.channel_host import (
    CommandChannel,
    OperatorChannelHost,
    _lock_path,
    _release_lock,
    _try_acquire_lock,
    config_identity_from_token,
)


# ── CommandChannel protocol conformance ──────────────────────────


class TestProtocolConformance:
    def test_telegram_implements_protocol(self):
        from supervisor.adapters.telegram_command import TelegramCommandChannel
        ch = TelegramCommandChannel(bot_token="tok", chat_id="123")
        assert isinstance(ch, CommandChannel)
        assert hasattr(ch, "config_identity")
        assert hasattr(ch, "start")
        assert hasattr(ch, "stop")
        assert hasattr(ch, "notify")

    def test_lark_implements_protocol(self):
        from supervisor.adapters.lark_command import LarkCommandChannel
        ch = LarkCommandChannel(
            app_id="cli_xxx", app_secret="secret",
            allowed_chat_ids=["oc_xxx"], callback_port=0,
        )
        assert isinstance(ch, CommandChannel)
        assert hasattr(ch, "config_identity")

    def test_telegram_config_identity_deterministic(self):
        from supervisor.adapters.telegram_command import TelegramCommandChannel
        ch1 = TelegramCommandChannel(bot_token="tok_abc", chat_id="123")
        ch2 = TelegramCommandChannel(bot_token="tok_abc", chat_id="456")
        assert ch1.config_identity == ch2.config_identity  # same token

    def test_lark_config_identity_deterministic(self):
        from supervisor.adapters.lark_command import LarkCommandChannel
        ch1 = LarkCommandChannel(
            app_id="cli_same", app_secret="s1",
            allowed_chat_ids=["oc_1"], callback_port=0,
        )
        ch2 = LarkCommandChannel(
            app_id="cli_same", app_secret="s2",
            allowed_chat_ids=["oc_2"], callback_port=0,
        )
        assert ch1.config_identity == ch2.config_identity  # same app_id

    def test_different_tokens_different_identity(self):
        from supervisor.adapters.telegram_command import TelegramCommandChannel
        ch1 = TelegramCommandChannel(bot_token="tok_a", chat_id="123")
        ch2 = TelegramCommandChannel(bot_token="tok_b", chat_id="123")
        assert ch1.config_identity != ch2.config_identity


# ── Advisory file locking ────────────────────────────────────────


class TestAdvisoryLocking:
    def test_acquire_and_release(self, tmp_path):
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            fh = _try_acquire_lock("test_identity")
            assert fh is not None
            assert _lock_path("test_identity").exists()
            _release_lock(fh)

    def test_double_acquire_fails(self, tmp_path):
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            fh1 = _try_acquire_lock("test_identity")
            assert fh1 is not None
            fh2 = _try_acquire_lock("test_identity")
            assert fh2 is None  # second acquire fails
            _release_lock(fh1)

    def test_release_allows_reacquire(self, tmp_path):
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            fh1 = _try_acquire_lock("test_identity")
            _release_lock(fh1)
            fh2 = _try_acquire_lock("test_identity")
            assert fh2 is not None
            _release_lock(fh2)

    def test_different_identities_independent(self, tmp_path):
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            fh1 = _try_acquire_lock("identity_a")
            fh2 = _try_acquire_lock("identity_b")
            assert fh1 is not None
            assert fh2 is not None
            _release_lock(fh1)
            _release_lock(fh2)


# ── OperatorChannelHost ──────────────────────────────────────────


def _mock_channel(identity: str = "test_id") -> MagicMock:
    ch = MagicMock(spec=["start", "stop", "notify", "config_identity"])
    ch.config_identity = identity
    return ch


class TestOperatorChannelHost:
    def test_start_acquires_lock_and_starts(self, tmp_path):
        ch = _mock_channel()
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            host = OperatorChannelHost([ch])
            host.start()
            ch.start.assert_called_once()
            assert len(host.channels) == 1
            host.stop()

    def test_stop_releases_lock(self, tmp_path):
        ch = _mock_channel()
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            host = OperatorChannelHost([ch])
            host.start()
            host.stop()
            ch.stop.assert_called_once()
            # Lock released: can re-acquire
            fh = _try_acquire_lock("test_id")
            assert fh is not None
            _release_lock(fh)

    def test_skips_channel_when_lock_held(self, tmp_path):
        ch = _mock_channel()
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            # Simulate another process holding the lock
            fh = _try_acquire_lock("test_id")
            assert fh is not None
            host = OperatorChannelHost([ch])
            host.start()
            ch.start.assert_not_called()
            assert len(host.channels) == 0
            host.stop()
            _release_lock(fh)

    def test_multiple_channels_independent_locks(self, tmp_path):
        ch_a = _mock_channel("id_a")
        ch_b = _mock_channel("id_b")
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            # Hold lock for id_a
            fh = _try_acquire_lock("id_a")
            host = OperatorChannelHost([ch_a, ch_b])
            host.start()
            ch_a.start.assert_not_called()  # skipped
            ch_b.start.assert_called_once()  # started
            assert len(host.channels) == 1
            host.stop()
            _release_lock(fh)

    def test_notify_forwards_to_started_only(self, tmp_path):
        ch_a = _mock_channel("id_a")
        ch_b = _mock_channel("id_b")
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            fh = _try_acquire_lock("id_a")
            host = OperatorChannelHost([ch_a, ch_b])
            host.start()
            event = MagicMock()
            host.notify(event)
            ch_a.notify.assert_not_called()  # not started
            ch_b.notify.assert_called_once_with(event)
            host.stop()
            _release_lock(fh)

    def test_start_returns_self(self, tmp_path):
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            host = OperatorChannelHost([])
            assert host.start() is host

    def test_from_config_creates_telegram(self):
        from supervisor.config import RuntimeConfig
        config = RuntimeConfig(
            notification_channels=[
                {"kind": "telegram", "mode": "command", "bot_token": "tok", "chat_id": "123"},
            ]
        )
        host = OperatorChannelHost.from_config(config)
        assert len(host._channels) == 1
        from supervisor.adapters.telegram_command import TelegramCommandChannel
        assert isinstance(host._channels[0], TelegramCommandChannel)

    def test_from_config_skips_notify_mode(self):
        from supervisor.config import RuntimeConfig
        config = RuntimeConfig(
            notification_channels=[
                {"kind": "jsonl"},
                {"kind": "telegram", "bot_token": "tok", "chat_id": "123"},
            ]
        )
        host = OperatorChannelHost.from_config(config)
        assert len(host._channels) == 0


# ── NotificationManager purity ───────────────────────────────────


class TestNotificationManagerPurity:
    def test_no_create_command_channels(self):
        from supervisor.notifications import NotificationManager
        assert not hasattr(NotificationManager, "create_command_channels")

    def test_no_start_all(self):
        from supervisor.notifications import NotificationManager
        assert not hasattr(NotificationManager, "start_all")

    def test_no_stop_all(self):
        from supervisor.notifications import NotificationManager
        assert not hasattr(NotificationManager, "stop_all")

    def test_from_config_ignores_mode_command(self, tmp_path):
        from supervisor.config import RuntimeConfig
        from supervisor.notifications import NotificationManager
        config = RuntimeConfig(
            notification_channels=[
                {"kind": "jsonl"},
                {"kind": "telegram", "mode": "command", "bot_token": "tok", "chat_id": "123"},
            ]
        )
        manager = NotificationManager.from_config(config, runtime_root=tmp_path)
        # Only jsonl, not telegram command
        assert len(manager.channels) == 1

    def test_command_channels_passed_through(self, tmp_path):
        from supervisor.config import RuntimeConfig
        from supervisor.notifications import NotificationManager
        config = RuntimeConfig(notification_channels=[{"kind": "jsonl"}])
        shared = [MagicMock()]
        manager = NotificationManager.from_config(
            config, runtime_root=tmp_path, command_channels=shared,
        )
        assert len(manager.channels) == 2
        assert manager.channels[1] is shared[0]

    def test_no_command_channels_is_none_safe(self, tmp_path):
        from supervisor.config import RuntimeConfig
        from supervisor.notifications import NotificationManager
        config = RuntimeConfig(notification_channels=[{"kind": "jsonl"}])
        manager = NotificationManager.from_config(config, runtime_root=tmp_path)
        assert len(manager.channels) == 1


# ── Dispatch-only routing ────────────────────────────────────────


class TestDispatchOnlyRouting:
    """Verify that command channel adapters do not import operator actions directly.

    All command routing must go through dispatch_command().
    """

    def _get_imports(self, module_path: str) -> set[str]:
        """Parse a Python file and return all imported names."""
        source = Path(module_path).read_text()
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
        return imports

    def test_telegram_does_not_import_actions_directly(self):
        import supervisor.adapters.telegram_command as mod
        imports = self._get_imports(mod.__file__)
        assert "supervisor.operator.actions" not in imports
        assert "supervisor.operator.run_context" not in imports

    def test_lark_does_not_import_actions_directly(self):
        import supervisor.adapters.lark_command as mod
        imports = self._get_imports(mod.__file__)
        assert "supervisor.operator.actions" not in imports
        assert "supervisor.operator.run_context" not in imports
