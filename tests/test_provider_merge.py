"""Provider Instance merge semantics — contract tests.

Freezes the behavior specified in
docs/plans/2026-04-17-im-command-channel-identity-and-merge-semantics.md.

Key rules under test:
- One Provider Instance (bot_token / app_id) = one logical adapter.
- Multiple config entries for the same Provider Instance MERGE:
    * conversation targets (chat_id / chat_ids) union
    * allowed_chat_ids union
    * allowed_user_ids union
- Transport-critical fields must match exactly:
    * language
    * Lark: app_secret, callback_port, verification_token, encrypt_key
- Mismatch fails closed at config build time.
- notify() fans out to EVERY merged conversation target.
- Inbound transport is singleton per Provider Instance; outbound fans out.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from supervisor.config import RuntimeConfig
from supervisor.notifications import NotificationEvent
from supervisor.operator.channel_host import OperatorChannelHost


# ── Telegram: same bot, multiple chats ──────────────────────────


class TestTelegramSameBotMultipleChats:
    def test_two_chats_same_token_merge_into_one_adapter(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_A",
             "allowed_chat_ids": ["chat_A"], "allowed_user_ids": ["alice"]},
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_B",
             "allowed_chat_ids": ["chat_B"], "allowed_user_ids": ["bob"]},
        ])
        host = OperatorChannelHost.from_config(config)
        # One Provider Instance → one adapter
        assert len(host.channels) == 1
        ch = host.channels[0]
        # Conversation targets unioned
        assert set(ch.conversation_targets) == {"chat_A", "chat_B"}
        # allowed_chat_ids unioned
        assert set(ch.auth.allowed_chat_ids) == {"chat_A", "chat_B"}
        # allowed_user_ids unioned
        assert set(ch.auth.allowed_user_ids) == {"alice", "bob"}

    def test_notify_fans_out_to_all_merged_targets(self):
        """One notify() call delivers to every merged conversation target."""
        config = RuntimeConfig(notification_channels=[
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_A"},
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_B"},
        ])
        host = OperatorChannelHost.from_config(config)
        ch = host.channels[0]
        sent: list[tuple[str, str]] = []
        ch._send_message = lambda chat_id, text, **kw: sent.append((chat_id, text)) or {}
        ch.notify(NotificationEvent(
            event_type="human_pause", run_id="r1",
            top_state="PAUSED_FOR_HUMAN", reason="need review",
            next_action="resume run", pane_target="",
        ))
        targets = {chat_id for chat_id, _ in sent}
        assert targets == {"chat_A", "chat_B"}

    def test_merged_auth_covers_all_chats(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_A",
             "allowed_chat_ids": ["chat_A"], "allowed_user_ids": ["alice"]},
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_B",
             "allowed_chat_ids": ["chat_B"], "allowed_user_ids": ["bob"]},
        ])
        host = OperatorChannelHost.from_config(config)
        auth = host.channels[0].auth
        # Merged allowlists: both chats and both users are on the lists
        assert set(auth.allowed_chat_ids) == {"chat_A", "chat_B"}
        assert set(auth.allowed_user_ids) == {"alice", "bob"}
        # alice from chat_A, bob from chat_B — both merged
        assert auth.is_authorized("chat_A", "alice")
        assert auth.is_authorized("chat_B", "bob")
        # Cross-merge: alice in chat_B, bob in chat_A — both merged allowlists apply
        assert auth.is_authorized("chat_A", "bob")
        assert auth.is_authorized("chat_B", "alice")


# ── Lark: same app, multiple chats ──────────────────────────────


class TestLarkSameAppMultipleChats:
    def test_two_chats_same_app_merge_into_one_adapter(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "secret",
             "allowed_chat_ids": ["oc_proj"],
             "allowed_user_ids": ["ou_1"]},
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "secret",
             "allowed_chat_ids": ["oc_ops"],
             "allowed_user_ids": ["ou_2"]},
        ])
        host = OperatorChannelHost.from_config(config)
        assert len(host.channels) == 1
        ch = host.channels[0]
        assert set(ch.conversation_targets) == {"oc_proj", "oc_ops"}
        assert set(ch.auth.allowed_chat_ids) == {"oc_proj", "oc_ops"}
        assert set(ch.auth.allowed_user_ids) == {"ou_1", "ou_2"}

    def test_notify_fans_out_to_all_merged_lark_chats(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "secret",
             "allowed_chat_ids": ["oc_proj"]},
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "secret",
             "allowed_chat_ids": ["oc_ops"]},
        ])
        host = OperatorChannelHost.from_config(config)
        ch = host.channels[0]
        sent: list[str] = []
        # Intercept the per-target send path
        original = ch._send_alert_card
        ch._send_alert_card = lambda chat_id, event: sent.append(chat_id)
        ch.notify(NotificationEvent(
            event_type="human_pause", run_id="r1",
            top_state="PAUSED_FOR_HUMAN", reason="need review",
            next_action="resume", pane_target="",
        ))
        assert set(sent) == {"oc_proj", "oc_ops"}


# ── Transport-critical field conflicts fail closed ──────────────


class TestExactMatchConflictFailsClosed:
    def test_telegram_language_conflict_raises(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_A", "language": "zh"},
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_B", "language": "en"},
        ])
        with pytest.raises(ValueError, match="(?i)language"):
            OperatorChannelHost.from_config(config)

    def test_lark_language_conflict_raises(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "s", "allowed_chat_ids": ["oc_a"],
             "language": "zh"},
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "s", "allowed_chat_ids": ["oc_b"],
             "language": "en"},
        ])
        with pytest.raises(ValueError, match="(?i)language"):
            OperatorChannelHost.from_config(config)

    def test_lark_callback_port_conflict_raises(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "s", "allowed_chat_ids": ["oc_a"],
             "callback_port": 9876},
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "s", "allowed_chat_ids": ["oc_b"],
             "callback_port": 9999},
        ])
        with pytest.raises(ValueError, match="(?i)callback_port"):
            OperatorChannelHost.from_config(config)

    def test_lark_app_secret_conflict_raises(self):
        """Same app_id with different app_secret must fail closed."""
        config = RuntimeConfig(notification_channels=[
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "secret_v1",
             "allowed_chat_ids": ["oc_a"]},
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "secret_v2",
             "allowed_chat_ids": ["oc_b"]},
        ])
        with pytest.raises(ValueError, match="(?i)app_secret"):
            OperatorChannelHost.from_config(config)

    def test_lark_verification_token_conflict_raises(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "s", "allowed_chat_ids": ["oc_a"],
             "verification_token": "vt_one"},
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "s", "allowed_chat_ids": ["oc_b"],
             "verification_token": "vt_two"},
        ])
        with pytest.raises(ValueError, match="(?i)verification_token"):
            OperatorChannelHost.from_config(config)

    def test_lark_encrypt_key_conflict_raises(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "s", "allowed_chat_ids": ["oc_a"],
             "encrypt_key": "ek_one"},
            {"kind": "lark", "mode": "command",
             "app_id": "cli_x", "app_secret": "s", "allowed_chat_ids": ["oc_b"],
             "encrypt_key": "ek_two"},
        ])
        with pytest.raises(ValueError, match="(?i)encrypt_key"):
            OperatorChannelHost.from_config(config)


# ── Cross-process: non-owner still fans out to all targets ──────


class TestNonOwnerFanout:
    def test_non_owner_process_still_notifies_all_merged_targets(self, tmp_path):
        """Process that loses the lock still sends outbound notifications
        to every merged conversation target."""
        config = RuntimeConfig(notification_channels=[
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_A"},
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_B"},
        ])
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            # Simulate "another process owns the transport" by grabbing
            # the lock ourselves before host.start().
            from supervisor.operator.channel_host import (
                _lock_path,
                _release_lock,
                _try_acquire_lock,
            )
            host = OperatorChannelHost.from_config(config)
            identity = host.channels[0].config_identity
            foreign_lock = _try_acquire_lock(identity)
            assert foreign_lock is not None

            host.start()
            # No transport owned by this host
            assert len(host.transport_owners) == 0
            # But the merged adapter is still present for outbound
            assert len(host.channels) == 1

            ch = host.channels[0]
            sent: list[str] = []
            ch._send_message = lambda chat_id, text, **kw: sent.append(chat_id) or {}
            host.notify(NotificationEvent(
                event_type="human_pause", run_id="r1",
                top_state="PAUSED_FOR_HUMAN", reason="x",
                next_action="y", pane_target="",
            ))
            assert set(sent) == {"chat_A", "chat_B"}

            host.stop()
            _release_lock(foreign_lock)

    def test_inbound_singleton_merged_allowlist_still_applies(self, tmp_path):
        """Only one process owns inbound, but the merged allowlist is the
        union of all config entries that share the Provider Instance."""
        config = RuntimeConfig(notification_channels=[
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_A",
             "allowed_chat_ids": ["chat_A"], "allowed_user_ids": ["alice"]},
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_x", "chat_id": "chat_B",
             "allowed_chat_ids": ["chat_B"], "allowed_user_ids": ["bob"]},
        ])
        with patch("supervisor.operator.channel_host.LOCK_DIR", str(tmp_path)):
            host = OperatorChannelHost.from_config(config)
            # Prevent real polling threads from starting; we only care
            # about singleton-plus-merged-auth semantics.
            host.channels[0].start = MagicMock()
            host.start()
            assert len(host.transport_owners) == 1
            auth = host.transport_owners[0].auth
            # Merged allowlists apply regardless of which process owns transport
            assert set(auth.allowed_chat_ids) == {"chat_A", "chat_B"}
            assert set(auth.allowed_user_ids) == {"alice", "bob"}
            assert auth.is_authorized("chat_A", "alice")
            assert auth.is_authorized("chat_B", "bob")
            assert auth.is_authorized("chat_A", "bob")
            host.stop()


# ── Unrelated Provider Instances stay independent ───────────────


class TestDifferentProviderInstancesStayIndependent:
    def test_different_bot_tokens_remain_separate_adapters(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_one", "chat_id": "chat_A"},
            {"kind": "telegram", "mode": "command",
             "bot_token": "tok_two", "chat_id": "chat_B"},
        ])
        host = OperatorChannelHost.from_config(config)
        assert len(host.channels) == 2

    def test_different_app_ids_remain_separate_lark_adapters(self):
        config = RuntimeConfig(notification_channels=[
            {"kind": "lark", "mode": "command",
             "app_id": "cli_one", "app_secret": "s",
             "allowed_chat_ids": ["oc_a"]},
            {"kind": "lark", "mode": "command",
             "app_id": "cli_two", "app_secret": "s",
             "allowed_chat_ids": ["oc_b"]},
        ])
        host = OperatorChannelHost.from_config(config)
        assert len(host.channels) == 2
