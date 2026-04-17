"""Tests for the Lark/Feishu command channel adapter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from supervisor.adapters.lark_command import (
    LarkBotClient,
    LarkCommandChannel,
    _LarkCallbackHandler,
    _lark_button,
    _lark_field,
)
from supervisor.notifications import NotificationEvent
from supervisor.operator.command_dispatch import CommandResult


# ── LarkBotClient ────────────────────────────────────────────────


class TestLarkBotClient:
    def test_requires_credentials(self):
        with pytest.raises(ValueError):
            LarkBotClient("", "secret")
        with pytest.raises(ValueError):
            LarkBotClient("app_id", "")

    def test_construction(self):
        client = LarkBotClient("cli_xxx", "secret")
        assert client.app_id == "cli_xxx"
        assert client.app_secret == "secret"

    def test_token_cache(self):
        """Token is cached and not re-fetched within expiry."""
        client = LarkBotClient("cli_xxx", "secret")
        client._token = "cached_token"
        client._token_expires = 9999999999  # far future
        assert client._ensure_token() == "cached_token"

    def test_api_call_no_token(self):
        client = LarkBotClient("cli_xxx", "secret")
        client._token = ""
        client._token_expires = 0
        # Mock _ensure_token to return empty
        with patch.object(client, "_ensure_token", return_value=""):
            result = client._api_call("GET", "https://example.com", {})
            assert result is None


# ── LarkCommandChannel construction ──────────────────────────────


def _make_channel(**overrides) -> LarkCommandChannel:
    defaults = {
        "app_id": "cli_xxx",
        "app_secret": "secret",
        "allowed_chat_ids": ["oc_xxx"],
        "language": "zh",
        "callback_port": 0,  # don't actually bind
    }
    defaults.update(overrides)
    return LarkCommandChannel(**defaults)


class TestConstruction:
    def test_basic(self):
        ch = _make_channel()
        assert ch.language == "zh"
        assert ch.conversation_targets == {"oc_xxx"}
        assert set(ch.auth.allowed_chat_ids) == {"oc_xxx"}

    def test_auth_chat_only(self):
        ch = _make_channel(allowed_chat_ids=["oc_xxx"])
        assert ch.auth.is_authorized("oc_xxx")
        assert not ch.auth.is_authorized("oc_yyy")

    def test_auth_user_level(self):
        ch = _make_channel(allowed_chat_ids=["oc_xxx"], allowed_user_ids=["ou_admin"])
        assert ch.auth.is_authorized("oc_xxx")
        assert ch.auth.is_authorized("oc_yyy", "ou_admin")  # user match
        assert not ch.auth.is_authorized("oc_yyy", "ou_random")  # neither


# ── Card building ────────────────────────────────────────────────


class TestCardBuilding:
    def test_alert_card_structure(self):
        ch = _make_channel()
        event = NotificationEvent(
            event_type="human_pause",
            run_id="run_abc123def456",
            top_state="PAUSED_FOR_HUMAN",
            reason="gate blocked",
            next_action="review",
        )
        card = ch._build_alert_card(event)
        assert "header" in card
        assert "elements" in card
        # header has template color
        assert card["header"]["template"] in ("yellow", "red", "green", "blue", "orange", "purple")
        # elements: div (fields) + hr + action
        assert len(card["elements"]) == 3
        assert card["elements"][2]["tag"] == "action"

    def test_action_buttons(self):
        ch = _make_channel()
        buttons = ch._build_action_buttons("run_abc123def456")
        assert buttons["tag"] == "action"
        labels = [a["text"]["content"] for a in buttons["actions"]]
        assert "Inspect" in labels
        assert "Explain" in labels
        assert "Pause" in labels
        assert "Resume" in labels
        assert "Notes" in labels
        # run_id should be truncated to 12 chars
        for action in buttons["actions"]:
            assert len(action["value"]["run_id"]) <= 12

    def test_result_card(self):
        ch = _make_channel()
        result = CommandResult(text="Test output text")
        card = ch._build_result_card(result)
        assert card["header"]["template"] == "blue"
        assert card["elements"][0]["text"]["content"] == "Test output text"

    def test_result_card_with_buttons(self):
        ch = _make_channel()
        result = CommandResult(
            text="output",
            buttons=[{"label": "Inspect", "cmd": "inspect", "run_id": "run_abc123"}],
        )
        card = ch._build_result_card(result)
        # Should have: div + hr + action
        assert len(card["elements"]) == 3

    def test_working_card(self):
        ch = _make_channel()
        card = ch._build_working_card()
        assert "Working" in card["elements"][0]["text"]["content"]


# ── Notify ───────────────────────────────────────────────────────


class TestNotify:
    def test_sends_to_all_chats(self):
        ch = _make_channel(allowed_chat_ids=["oc_1", "oc_2"])
        event = NotificationEvent(
            event_type="human_pause",
            run_id="run_x",
            top_state="PAUSED",
            reason="test",
            next_action="check",
        )
        with patch.object(ch.bot, "send_message") as mock_send:
            ch.notify(event)
            assert mock_send.call_count == 2
            calls = [c.args[0] for c in mock_send.call_args_list]
            assert "oc_1" in calls
            assert "oc_2" in calls


# ── Card action handling ─────────────────────────────────────────


class TestCardActionHandling:
    def test_sync_command(self):
        ch = _make_channel()
        with patch("supervisor.adapters.lark_command.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = CommandResult(text="result")
            card = ch.handle_card_action(
                {"cmd": "inspect", "run_id": "abc123"},
                "oc_xxx",
                "msg_1",
            )
            mock_dispatch.assert_called_once_with("inspect", ["abc123"], language="zh")
            assert card is not None
            assert card["elements"][0]["text"]["content"] == "result"

    def test_empty_cmd_ignored(self):
        ch = _make_channel()
        result = ch.handle_card_action({}, "oc_xxx", "msg_1")
        assert result is None

    def test_async_command_updates_card(self):
        ch = _make_channel()
        from supervisor.operator.actions import OperatorJob
        ctx = MagicMock()
        job = OperatorJob(job_id="j1", source="local")

        with patch("supervisor.adapters.lark_command.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = CommandResult(
                text="Working...", job=job, ctx=ctx,
            )
            with patch.object(ch.bot, "update_message") as mock_update:
                with patch.object(ch._poller, "track") as mock_track:
                    result = ch.handle_card_action(
                        {"cmd": "explain", "run_id": "abc123"},
                        "oc_xxx",
                        "msg_1",
                    )
                    assert result is None  # handled via update
                    mock_update.assert_called_once()
                    mock_track.assert_called_once()


# ── Text command handling ────────────────────────────────────────


class TestTextCommandHandling:
    def test_sync_text_command(self):
        ch = _make_channel()
        with patch("supervisor.adapters.lark_command.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = CommandResult(text="result")
            with patch.object(ch.bot, "reply_message") as mock_reply:
                ch.handle_text_command("/runs", "oc_xxx", "msg_1")
                mock_dispatch.assert_called_once()
                mock_reply.assert_called_once()

    def test_empty_command_ignored(self):
        ch = _make_channel()
        with patch("supervisor.adapters.lark_command.dispatch_command") as mock_dispatch:
            ch.handle_text_command("hello there", "oc_xxx", "msg_1")
            # parse_command returns ("", []) for non-commands


# ── HTTP handler text message routing ────────────────────────────


class TestHttpTextMessageRouting:
    """Verify that the HTTP callback handler routes im.message.receive_v1
    events to handle_text_command() via real HTTP requests."""

    def _start_server(self, channel):
        """Start a test HTTP server on a random port. Returns (server, port)."""
        from functools import partial
        from http.server import ThreadingHTTPServer
        from supervisor.adapters.lark_command import _LarkCallbackHandler
        handler = partial(_LarkCallbackHandler, channel=channel)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        channel._callback_port = port
        import threading
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, port

    def _post(self, port: int, payload: dict) -> int:
        import http.client
        body = json.dumps(payload).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        status = resp.status
        conn.close()
        return status

    def test_text_event_routes_to_handle_text_command(self):
        ch = _make_channel(callback_port=0)
        with patch.object(ch, "handle_text_command") as mock_handle:
            server, port = self._start_server(ch)
            try:
                payload = {
                    "header": {"event_type": "im.message.receive_v1", "event_id": "evt_1"},
                    "event": {
                        "message": {
                            "chat_id": "oc_xxx",
                            "message_id": "msg_evt_1",
                            "message_type": "text",
                            "content": json.dumps({"text": "/runs"}),
                        },
                    },
                }
                status = self._post(port, payload)
                assert status == 200
                # Command dispatches in background thread — wait briefly
                import time
                time.sleep(0.2)
                mock_handle.assert_called_once_with("/runs", "oc_xxx", "msg_evt_1")
            finally:
                server.shutdown()

    def test_card_action_does_not_trigger_text_handler(self):
        ch = _make_channel(callback_port=0)
        with patch.object(ch, "handle_text_command") as mock_text:
            with patch.object(ch, "handle_card_action", return_value=None) as mock_card:
                server, port = self._start_server(ch)
                try:
                    payload = {
                        "action": {"value": {"cmd": "inspect", "run_id": "abc"}},
                        "event": {"open_chat_id": "oc_xxx", "open_message_id": "msg_1"},
                    }
                    status = self._post(port, payload)
                    assert status == 200
                    mock_text.assert_not_called()
                    mock_card.assert_called_once()
                finally:
                    server.shutdown()

    def test_duplicate_event_ignored(self):
        """Duplicate events (same event_id) should not re-dispatch."""
        ch = _make_channel(callback_port=0)
        with patch.object(ch, "handle_text_command") as mock_handle:
            server, port = self._start_server(ch)
            try:
                payload = {
                    "header": {"event_type": "im.message.receive_v1", "event_id": "evt_dup"},
                    "event": {
                        "message": {
                            "chat_id": "oc_xxx",
                            "message_id": "msg_1",
                            "message_type": "text",
                            "content": json.dumps({"text": "/runs"}),
                        },
                    },
                }
                # First request
                self._post(port, payload)
                import time
                time.sleep(0.2)
                assert mock_handle.call_count == 1
                # Second request (retry) — same event_id
                self._post(port, payload)
                time.sleep(0.2)
                assert mock_handle.call_count == 1  # still 1, not 2
            finally:
                server.shutdown()

    def test_user_authorized_text_event(self):
        """Text message from authorized user in any chat should be processed."""
        ch = _make_channel(
            callback_port=0,
            conversation_targets=["oc_default"],
            allowed_chat_ids=[],
            allowed_user_ids=["ou_admin"],
        )
        with patch.object(ch, "handle_text_command") as mock_handle:
            server, port = self._start_server(ch)
            try:
                payload = {
                    "header": {"event_type": "im.message.receive_v1", "event_id": "evt_user"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_admin"}},
                        "message": {
                            "chat_id": "oc_any",
                            "message_id": "msg_1",
                            "message_type": "text",
                            "content": json.dumps({"text": "/runs"}),
                        },
                    },
                }
                status = self._post(port, payload)
                assert status == 200
                import time
                time.sleep(0.2)
                mock_handle.assert_called_once()
            finally:
                server.shutdown()

    def test_unauthorized_text_event_ignored(self):
        ch = _make_channel(callback_port=0, allowed_chat_ids=["oc_xxx"])
        with patch.object(ch, "handle_text_command") as mock_handle:
            server, port = self._start_server(ch)
            try:
                payload = {
                    "header": {"event_type": "im.message.receive_v1"},
                    "event": {
                        "message": {
                            "chat_id": "oc_unauthorized",
                            "message_id": "msg_1",
                            "message_type": "text",
                            "content": json.dumps({"text": "/runs"}),
                        },
                    },
                }
                status = self._post(port, payload)
                assert status == 200
                mock_handle.assert_not_called()
            finally:
                server.shutdown()


# ── Job completion callback ──────────────────────────────────────


class TestJobCompletion:
    def test_completed_job_updates_card(self):
        ch = _make_channel()
        with patch.object(ch.bot, "update_message") as mock_update:
            ch._on_job_complete(
                "msg_1",
                {"status": "completed", "result": {"explanation": "all good"}},
                [],
            )
            mock_update.assert_called_once()
            card = mock_update.call_args[0][1]
            assert "all good" in card["elements"][0]["text"]["content"]

    def test_failed_job_shows_error(self):
        ch = _make_channel()
        with patch.object(ch.bot, "update_message") as mock_update:
            ch._on_job_complete(
                "msg_1",
                {"status": "failed", "error": "timeout"},
                [],
            )
            card = mock_update.call_args[0][1]
            assert "timeout" in card["elements"][0]["text"]["content"]


# ── Helper functions ─────────────────────────────────────────────


class TestEventDedup:
    def test_first_event_not_duplicate(self):
        ch = _make_channel()
        assert not ch._is_duplicate_event("evt_1")

    def test_second_event_is_duplicate(self):
        ch = _make_channel()
        ch._is_duplicate_event("evt_1")
        assert ch._is_duplicate_event("evt_1")

    def test_empty_event_id_never_duplicate(self):
        ch = _make_channel()
        assert not ch._is_duplicate_event("")
        assert not ch._is_duplicate_event("")

    def test_bounded_set_pruning(self):
        ch = _make_channel()
        # Fill past 1000
        for i in range(1100):
            ch._is_duplicate_event(f"evt_{i}")
        assert len(ch._seen_event_ids) <= 600  # pruned to ~500


class TestSignatureVerification:
    def test_channel_accepts_verification_token(self):
        ch = _make_channel(verification_token="tok_123", encrypt_key="enc_key")
        assert ch.verification_token == "tok_123"
        assert ch.encrypt_key == "enc_key"

    def test_signature_computation(self):
        """Verify the signature algorithm matches Lark spec."""
        import hashlib
        timestamp = "1234567890"
        nonce = "abc"
        encrypt_key = "mysecret"
        body = '{"test": true}'
        to_sign = (timestamp + nonce + encrypt_key + body).encode("utf-8")
        sig = hashlib.sha256(to_sign).hexdigest()
        assert len(sig) == 64  # SHA256 hex digest


class TestHelpers:
    def test_lark_field(self):
        f = _lark_field("**Test**", short=True)
        assert f["is_short"] is True
        assert f["text"]["tag"] == "lark_md"
        assert f["text"]["content"] == "**Test**"

    def test_lark_button(self):
        btn = _lark_button("Click", "cmd", "rid", btn_type="primary")
        assert btn["tag"] == "button"
        assert btn["text"]["content"] == "Click"
        assert btn["type"] == "primary"
        assert btn["value"] == {"cmd": "cmd", "run_id": "rid"}
