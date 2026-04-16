"""Tests for the Telegram command channel adapter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from supervisor.adapters.telegram_command import TelegramCommandChannel
from supervisor.notifications import NotificationEvent
from supervisor.operator.command_dispatch import CommandResult


def _make_channel(**overrides) -> TelegramCommandChannel:
    defaults = {
        "bot_token": "123:ABC",
        "chat_id": "-100",
        "allowed_chat_ids": ["-100"],
        "language": "zh",
    }
    defaults.update(overrides)
    return TelegramCommandChannel(**defaults)


# ── Construction ─────────────────────────────────────────────────


class TestConstruction:
    def test_basic(self):
        ch = _make_channel()
        assert ch.bot_token == "123:ABC"
        assert ch.chat_id == "-100"

    def test_missing_bot_token(self):
        with pytest.raises(ValueError, match="bot_token"):
            TelegramCommandChannel(bot_token="", chat_id="-100")

    def test_missing_chat_id(self):
        with pytest.raises(ValueError, match="chat_id"):
            TelegramCommandChannel(bot_token="123:ABC", chat_id="")

    def test_default_language(self):
        ch = _make_channel()
        assert ch.language == "zh"


# ── Auth ─────────────────────────────────────────────────────────


class TestAuth:
    def test_authorized_chat(self):
        ch = _make_channel(allowed_chat_ids=["-100"])
        assert ch.auth.is_authorized("-100")

    def test_unauthorized_chat(self):
        ch = _make_channel(allowed_chat_ids=["-100"])
        assert not ch.auth.is_authorized("-999")

    def test_user_id_auth(self):
        ch = _make_channel(allowed_user_ids=["42"])
        assert ch.auth.is_authorized("any", "42")


# ── Notify (outbound alert) ──────────────────────────────────────


class TestNotify:
    def test_sends_alert_with_keyboard(self):
        ch = _make_channel()
        event = NotificationEvent(
            event_type="human_pause",
            run_id="run_abc123def456",
            top_state="PAUSED_FOR_HUMAN",
            reason="gate blocked",
            next_action="review and resume",
        )
        with patch.object(ch, "_send_message") as mock_send:
            ch.notify(event)
            mock_send.assert_called_once()
            args, kwargs = mock_send.call_args
            assert args[0] == "-100"  # chat_id
            assert "PAUSED" in args[1]
            assert "reply_markup" in kwargs
            keyboard = kwargs["reply_markup"]
            assert "inline_keyboard" in keyboard
            # Should have 2 rows
            assert len(keyboard["inline_keyboard"]) == 2

    def test_alert_keyboard_buttons(self):
        ch = _make_channel()
        keyboard = ch._build_alert_keyboard("run_abc123def456")
        buttons = keyboard["inline_keyboard"]
        # Flatten
        all_labels = [b["text"] for row in buttons for b in row]
        assert "Inspect" in all_labels
        assert "Explain" in all_labels
        assert "Pause" in all_labels
        assert "Resume" in all_labels

    def test_callback_data_compact(self):
        ch = _make_channel()
        btn = ch._cb_button("Inspect", "inspect", "abc123def456")
        data = json.loads(btn["callback_data"])
        assert data["c"] == "inspect"
        assert data["r"] == "abc123def456"
        # Must fit 64 bytes
        assert len(btn["callback_data"]) <= 64


# ── Handle message (inbound text command) ────────────────────────


class TestHandleMessage:
    def test_dispatches_command(self):
        ch = _make_channel()
        msg = {
            "chat": {"id": -100},
            "from": {"id": 42},
            "text": "/help",
        }
        with patch.object(ch, "_send_message") as mock_send:
            with patch("supervisor.adapters.telegram_command.dispatch_command") as mock_dispatch:
                mock_dispatch.return_value = CommandResult(text="help text")
                ch._handle_message(msg)
                mock_dispatch.assert_called_once_with("help", [], language="zh")

    def test_unauthorized_silently_ignored(self):
        ch = _make_channel(allowed_chat_ids=["-100"])
        msg = {
            "chat": {"id": -999},
            "from": {"id": 1},
            "text": "/runs",
        }
        with patch("supervisor.adapters.telegram_command.dispatch_command") as mock_dispatch:
            ch._handle_message(msg)
            mock_dispatch.assert_not_called()

    def test_empty_text_ignored(self):
        ch = _make_channel()
        msg = {"chat": {"id": -100}, "from": {"id": 1}, "text": ""}
        with patch("supervisor.adapters.telegram_command.dispatch_command") as mock_dispatch:
            ch._handle_message(msg)
            mock_dispatch.assert_not_called()

    def test_non_command_ignored(self):
        ch = _make_channel()
        msg = {"chat": {"id": -100}, "from": {"id": 1}, "text": "hello there"}
        with patch("supervisor.adapters.telegram_command.parse_command") as mock_parse:
            mock_parse.return_value = ("", [])
            ch._handle_message(msg)
            # no dispatch since parse returned empty cmd


# ── Handle callback (button press) ───────────────────────────────


class TestHandleCallback:
    def test_dispatches_button_command(self):
        ch = _make_channel()
        callback = {
            "id": "cb_1",
            "from": {"id": 42},
            "message": {
                "chat": {"id": -100},
                "message_id": 999,
            },
            "data": json.dumps({"c": "inspect", "r": "abc123"}),
        }
        with patch.object(ch, "_answer_callback"):
            with patch.object(ch, "_edit_message") as mock_edit:
                with patch("supervisor.adapters.telegram_command.dispatch_command") as mock_dispatch:
                    mock_dispatch.return_value = CommandResult(text="result text")
                    ch._handle_callback(callback)
                    mock_dispatch.assert_called_once_with("inspect", ["abc123"], language="zh")
                    mock_edit.assert_called_once()

    def test_unauthorized_callback_rejected(self):
        ch = _make_channel(allowed_chat_ids=["-100"])
        callback = {
            "id": "cb_1",
            "from": {"id": 42},
            "message": {"chat": {"id": -999}, "message_id": 1},
            "data": json.dumps({"c": "inspect", "r": "x"}),
        }
        with patch.object(ch, "_answer_callback") as mock_answer:
            with patch("supervisor.adapters.telegram_command.dispatch_command") as mock_dispatch:
                ch._handle_callback(callback)
                mock_dispatch.assert_not_called()
                mock_answer.assert_called_once_with("cb_1", "Unauthorized")


# ── Async job flow ───────────────────────────────────────────────


class TestAsyncJobFlow:
    def test_send_result_with_job(self):
        ch = _make_channel()
        from supervisor.operator.actions import OperatorJob
        ctx = MagicMock()
        job = OperatorJob(job_id="j1", source="local")
        result = CommandResult(text="Working...", job=job, ctx=ctx)

        with patch.object(ch, "_send_message") as mock_send:
            mock_send.return_value = {"result": {"message_id": 42}}
            with patch.object(ch._poller, "track") as mock_track:
                ch._send_result("-100", result)
                mock_send.assert_called_once()
                mock_track.assert_called_once()

    def test_on_job_complete_edits_message(self):
        ch = _make_channel()
        with patch.object(ch, "_edit_message") as mock_edit:
            ch._on_job_complete(
                "-100", 42,
                {"status": "completed", "result": {"explanation": "all good"}},
                None,
            )
            mock_edit.assert_called_once()
            text = mock_edit.call_args[0][2]
            assert "all good" in text

    def test_on_job_failed(self):
        ch = _make_channel()
        with patch.object(ch, "_edit_message") as mock_edit:
            ch._on_job_complete(
                "-100", 42,
                {"status": "failed", "error": "boom"},
                None,
            )
            text = mock_edit.call_args[0][2]
            assert "boom" in text


# ── Format helpers ───────────────────────────────────────────────


class TestFormatting:
    def test_format_alert_escapes(self):
        ch = _make_channel()
        event = NotificationEvent(
            event_type="human_pause",
            run_id="run_test-123",
            top_state="PAUSED_FOR_HUMAN",
            reason="test reason",
            next_action="resume",
        )
        text = ch._format_alert(event)
        # Should contain run_id in code block
        assert "run_test" in text

    def test_format_text_result_working(self):
        ch = _make_channel()
        result = CommandResult(text="Working...", job=MagicMock())
        text = ch._format_text_result(result)
        assert "Working" in text

    def test_format_text_result_normal(self):
        ch = _make_channel()
        result = CommandResult(text="hello world")
        text = ch._format_text_result(result)
        assert "hello" in text
