"""Tests for Telegram and Lark notification channel adapters."""

import json
from unittest.mock import patch, MagicMock

import pytest

from supervisor.notifications import NotificationEvent, NotificationManager
from supervisor.adapters.telegram_channel import (
    TelegramNotificationChannel,
    _escape_md,
    _event_emoji,
)
from supervisor.adapters.lark_channel import (
    LarkNotificationChannel,
    _event_color,
    _event_emoji as lark_event_emoji,
)


def _make_event(**overrides):
    base = {
        "event_type": "human_pause",
        "run_id": "run_abc123",
        "top_state": "PAUSED_FOR_HUMAN",
        "reason": "verification failed",
        "next_action": "thin-supervisor run resume --spec s.yaml --pane %5",
        "workspace_root": "/tmp/workspace",
    }
    base.update(overrides)
    return NotificationEvent(**base)


# ── Telegram tests ─────────────────────────────────────────────────

class TestTelegramChannel:
    def test_init_requires_bot_token(self):
        with pytest.raises(ValueError, match="bot_token"):
            TelegramNotificationChannel(bot_token="", chat_id="123")

    def test_init_requires_chat_id(self):
        with pytest.raises(ValueError, match="chat_id"):
            TelegramNotificationChannel(bot_token="tok", chat_id="")

    def test_format_message(self):
        ch = TelegramNotificationChannel(bot_token="tok", chat_id="123")
        event = _make_event()
        msg = ch._format_message(event)

        assert "thin\\-supervisor" in msg
        assert "run\\_abc123" in msg
        assert "PAUSED\\_FOR\\_HUMAN" in msg

    @patch("supervisor.adapters.telegram_channel.urllib_request.urlopen")
    def test_notify_calls_api(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ch = TelegramNotificationChannel(bot_token="123:ABC", chat_id="-100")
        ch.notify(_make_event())

        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert "api.telegram.org/bot123:ABC/sendMessage" in req.full_url
        body = json.loads(req.data)
        assert body["chat_id"] == "-100"
        assert body["parse_mode"] == "MarkdownV2"

    @patch("supervisor.adapters.telegram_channel.urllib_request.urlopen")
    def test_notify_handles_api_error(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": False, "description": "bad"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ch = TelegramNotificationChannel(bot_token="tok", chat_id="123")
        # Should not raise
        ch.notify(_make_event())

    @patch("supervisor.adapters.telegram_channel.urllib_request.urlopen")
    def test_notify_handles_network_error(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("connection refused")

        ch = TelegramNotificationChannel(bot_token="tok", chat_id="123")
        # Should not raise
        ch.notify(_make_event())


class TestTelegramHelpers:
    def test_escape_md(self):
        assert _escape_md("hello_world") == "hello\\_world"
        assert _escape_md("a*b") == "a\\*b"
        assert _escape_md("plain") == "plain"

    def test_event_emoji(self):
        assert _event_emoji("human_pause") == "🔴"
        assert _event_emoji("run_completed") == "✅"
        assert _event_emoji("unknown") == "📋"


# ── Lark tests ─────────────────────────────────────────────────────

class TestLarkChannel:
    def test_init_requires_webhook_url(self):
        with pytest.raises(ValueError, match="webhook_url"):
            LarkNotificationChannel(webhook_url="")

    def test_build_card(self):
        ch = LarkNotificationChannel(webhook_url="https://example.com/hook")
        event = _make_event()
        card = ch._build_card(event)

        assert card["msg_type"] == "interactive"
        header = card["card"]["header"]
        assert "PAUSED_FOR_HUMAN" in header["title"]["content"]
        assert header["template"] == "red"

        elements = card["card"]["elements"]
        fields = elements[0]["fields"]
        field_texts = [f["text"]["content"] for f in fields]
        assert any("run_abc123" in t for t in field_texts)

    @patch("supervisor.adapters.lark_channel.urllib_request.urlopen")
    def test_notify_calls_webhook(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"code": 0}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ch = LarkNotificationChannel(webhook_url="https://example.com/hook")
        ch.notify(_make_event())

        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.full_url == "https://example.com/hook"
        body = json.loads(req.data)
        assert body["msg_type"] == "interactive"

    @patch("supervisor.adapters.lark_channel.urllib_request.urlopen")
    def test_notify_handles_webhook_error(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"code": 9499, "msg": "invalid"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ch = LarkNotificationChannel(webhook_url="https://example.com/hook")
        # Should not raise
        ch.notify(_make_event())

    @patch("supervisor.adapters.lark_channel.urllib_request.urlopen")
    def test_notify_handles_network_error(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("timeout")

        ch = LarkNotificationChannel(webhook_url="https://example.com/hook")
        # Should not raise
        ch.notify(_make_event())


class TestLarkHelpers:
    def test_event_color(self):
        assert _event_color("human_pause") == "red"
        assert _event_color("run_completed") == "green"
        assert _event_color("unknown") == "blue"

    def test_event_emoji(self):
        assert lark_event_emoji("human_pause") == "🔴"
        assert lark_event_emoji("run_completed") == "✅"


# ── NotificationManager integration ──────────────────────────────

class TestNotificationManagerIntegration:
    def test_telegram_channel_from_config(self):
        class FakeConfig:
            notification_channels = [
                {"kind": "telegram", "bot_token": "tok", "chat_id": "123"},
            ]

        mgr = NotificationManager.from_config(FakeConfig(), runtime_root="/tmp")
        assert len(mgr.channels) == 1
        assert isinstance(mgr.channels[0], TelegramNotificationChannel)

    def test_lark_channel_from_config(self):
        class FakeConfig:
            notification_channels = [
                {"kind": "lark", "webhook_url": "https://example.com/hook"},
            ]

        mgr = NotificationManager.from_config(FakeConfig(), runtime_root="/tmp")
        assert len(mgr.channels) == 1
        assert isinstance(mgr.channels[0], LarkNotificationChannel)

    def test_mixed_channels_from_config(self):
        class FakeConfig:
            notification_channels = [
                {"kind": "jsonl"},
                {"kind": "telegram", "bot_token": "tok", "chat_id": "123"},
                {"kind": "lark", "webhook_url": "https://example.com/hook"},
            ]

        mgr = NotificationManager.from_config(FakeConfig(), runtime_root="/tmp")
        assert len(mgr.channels) == 3
