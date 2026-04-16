"""Lark/Feishu command channel — full operator control via Bot API.

Supports interactive card buttons on alert notifications and an HTTP
callback server for card action events.  Uses the Bot API (app_id +
app_secret) for sending and updating messages, replacing the simpler
webhook-only notification channel.

Uses urllib only (no external dependencies).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib import request as urllib_request
from urllib.error import URLError

from supervisor.adapters.lark_channel import _event_color, _event_emoji
from supervisor.notifications import NotificationEvent
from supervisor.operator.command_dispatch import (
    AsyncJobPoller,
    CommandAuth,
    CommandResult,
    dispatch_command,
    format_explanation_result,
    parse_command,
)

logger = logging.getLogger(__name__)

LARK_API = "https://open.feishu.cn/open-apis"


# ── Lark Bot API client ──────────────────────────────────────────


class LarkBotClient:
    """Low-level Lark Bot API client using urllib.

    Handles tenant_access_token management and message send/update.
    """

    def __init__(self, app_id: str, app_secret: str):
        if not app_id or not app_secret:
            raise ValueError("lark app_id and app_secret are required")
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: str = ""
        self._token_expires: float = 0
        self._token_lock = threading.Lock()

    def _ensure_token(self) -> str:
        """Get or refresh tenant_access_token (thread-safe)."""
        with self._token_lock:
            if self._token and time.time() < self._token_expires - 60:
                return self._token
            payload = {
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            }
            try:
                req = urllib_request.Request(
                    f"{LARK_API}/auth/v3/tenant_access_token/internal",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib_request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    if result.get("code") != 0:
                        logger.warning("Lark token refresh failed: %s", result.get("msg"))
                        return self._token
                    self._token = result.get("tenant_access_token", "")
                    self._token_expires = time.time() + result.get("expire", 7200)
                    return self._token
            except (URLError, OSError) as exc:
                logger.warning("Lark token refresh error: %s", exc)
                return self._token

    def _api_call(self, method: str, url: str, payload: dict | None = None) -> dict | None:
        """Call a Lark API endpoint."""
        token = self._ensure_token()
        if not token:
            logger.warning("Lark API call skipped: no token")
            return None
        try:
            data = json.dumps(payload).encode("utf-8") if payload else None
            req = urllib_request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                method=method,
            )
            with urllib_request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                code = result.get("code", -1)
                if code != 0:
                    logger.warning("Lark API error (%s): %s", url, result.get("msg", ""))
                return result
        except (URLError, OSError) as exc:
            logger.warning("Lark API call failed (%s): %s", url, exc)
        except Exception:
            logger.exception("Lark API unexpected error (%s)", url)
        return None

    def send_message(self, chat_id: str, msg_type: str, content: dict | str) -> dict | None:
        """Send a message to a chat."""
        payload = {
            "receive_id": chat_id,
            "msg_type": msg_type,
            "content": json.dumps(content) if isinstance(content, dict) else content,
        }
        return self._api_call(
            "POST",
            f"{LARK_API}/im/v1/messages?receive_id_type=chat_id",
            payload,
        )

    def update_message(self, message_id: str, content: dict | str) -> dict | None:
        """Update (edit) a message by ID."""
        payload = {
            "content": json.dumps(content) if isinstance(content, dict) else content,
        }
        return self._api_call(
            "PUT",
            f"{LARK_API}/im/v1/messages/{message_id}",
            payload,
        )

    def reply_message(self, message_id: str, msg_type: str, content: dict | str) -> dict | None:
        """Reply to a message."""
        payload = {
            "msg_type": msg_type,
            "content": json.dumps(content) if isinstance(content, dict) else content,
        }
        return self._api_call(
            "POST",
            f"{LARK_API}/im/v1/messages/{message_id}/reply",
            payload,
        )


# ── Lark Command Channel ─────────────────────────────────────────


class LarkCommandChannel:
    """Full operator command channel over Lark/Feishu Bot API.

    Config::

        notification_channels:
          - kind: lark
            mode: command
            app_id: "cli_xxx"
            app_secret: "secret"
            allowed_chat_ids: ["oc_xxx"]
            language: "zh"
            callback_port: 9876
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        allowed_chat_ids: list[str] | None = None,
        language: str = "zh",
        callback_port: int = 9876,
        verification_token: str = "",
        encrypt_key: str = "",
    ):
        self.bot = LarkBotClient(app_id, app_secret)
        self.auth = CommandAuth(allowed_chat_ids=allowed_chat_ids)
        self.language = language
        self.allowed_chat_ids = allowed_chat_ids or []
        self._callback_port = callback_port
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        self._stop_event = threading.Event()
        self._server_thread: threading.Thread | None = None
        self._server: ThreadingHTTPServer | None = None
        self._poller = AsyncJobPoller()

    # ── NotificationChannel protocol ──────────────────────────────

    def notify(self, event: NotificationEvent) -> None:
        """Send interactive card with action buttons to all allowed chats."""
        card = self._build_alert_card(event)
        for chat_id in self.allowed_chat_ids:
            self.bot.send_message(chat_id, "interactive", card)

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        """Start HTTP callback server and job poller."""
        if self._server_thread and self._server_thread.is_alive():
            return
        self._stop_event.clear()
        self._poller.start()
        handler = partial(_LarkCallbackHandler, channel=self)
        self._server = ThreadingHTTPServer(("0.0.0.0", self._callback_port), handler)
        self._server.timeout = 1
        self._server_thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._server_thread.start()
        logger.info("Lark command channel started on port %d", self._callback_port)

    def stop(self) -> None:
        self._stop_event.set()
        self._poller.stop()
        if self._server:
            self._server.shutdown()

    def _serve_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._server:
                self._server.handle_request()

    # ── Card building ─────────────────────────────────────────────

    def _build_alert_card(self, event: NotificationEvent) -> dict:
        """Build an interactive card with action buttons."""
        emoji = _event_emoji(event.event_type)
        color = _event_color(event.event_type)

        fields = [
            _lark_field(f"**Run:** `{event.run_id}`", short=True),
            _lark_field(f"**State:** {event.top_state}", short=True),
        ]
        if event.reason:
            fields.append(_lark_field(f"**Reason:** {event.reason}"))
        if event.next_action:
            fields.append(_lark_field(f"**Next:** `{event.next_action}`"))
        if event.workspace_root:
            fields.append(_lark_field(f"**Worktree:** `{event.workspace_root}`", short=True))

        buttons = self._build_action_buttons(event.run_id)

        return {
            "header": {
                "title": {"tag": "plain_text", "content": f"{emoji} [thin-supervisor] {event.top_state}"},
                "template": color,
            },
            "elements": [
                {"tag": "div", "fields": fields},
                {"tag": "hr"},
                buttons,
            ],
        }

    def _build_action_buttons(self, run_id: str) -> dict:
        """Build card action buttons element."""
        short_id = run_id[-12:] if len(run_id) > 12 else run_id
        return {
            "tag": "action",
            "actions": [
                _lark_button("Inspect", "inspect", short_id, btn_type="primary"),
                _lark_button("Explain", "explain", short_id),
                _lark_button("Drift", "drift", short_id),
                _lark_button("Pause", "pause", short_id, btn_type="danger"),
                _lark_button("Resume", "resume", short_id),
                _lark_button("Notes", "notes", short_id),
            ],
        }

    def _build_result_card(self, result: CommandResult) -> dict:
        """Build a card displaying a command result."""
        text = result.text
        elements: list[dict] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": text[:2000]}},
        ]
        if result.buttons:
            run_id = result.buttons[0].get("run_id", "") if result.buttons else ""
            if run_id:
                elements.append({"tag": "hr"})
                elements.append(self._build_action_buttons(run_id))
        return {
            "header": {
                "title": {"tag": "plain_text", "content": "thin-supervisor"},
                "template": "blue",
            },
            "elements": elements,
        }

    def _build_working_card(self) -> dict:
        """Build a 'Working...' placeholder card."""
        return {
            "header": {
                "title": {"tag": "plain_text", "content": "thin-supervisor"},
                "template": "blue",
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "Working..."}},
            ],
        }

    # ── Command handling ──────────────────────────────────────────

    def handle_card_action(self, action_value: dict, chat_id: str, message_id: str) -> dict | None:
        """Handle a card button action callback.  Returns response card or None."""
        cmd = action_value.get("cmd", "")
        run_id = action_value.get("run_id", "")
        if not cmd:
            return None

        result = dispatch_command(cmd, [run_id] if run_id else [], language=self.language)

        if result.job and result.ctx:
            # Async job: update card to "Working..." and track
            working_card = self._build_working_card()
            self.bot.update_message(message_id, working_card)
            self._poller.track(
                result.ctx,
                result.job,
                lambda r, mid=message_id, btns=result.buttons: self._on_job_complete(
                    mid, r, btns,
                ),
            )
            return None  # already handled via update

        # Sync result: return updated card
        return self._build_result_card(result)

    def handle_text_command(self, text: str, chat_id: str, message_id: str) -> None:
        """Handle an inbound text message as a command."""
        cmd, args = parse_command(text)
        if not cmd:
            return
        result = dispatch_command(cmd, args, language=self.language)

        if result.job and result.ctx:
            # Async: send working card, track job
            working_card = self._build_working_card()
            resp = self.bot.send_message(chat_id, "interactive", working_card)
            sent_msg_id = ""
            if resp and resp.get("code") == 0:
                sent_msg_id = resp.get("data", {}).get("message_id", "")
            if sent_msg_id:
                self._poller.track(
                    result.ctx,
                    result.job,
                    lambda r, mid=sent_msg_id, btns=result.buttons: self._on_job_complete(
                        mid, r, btns,
                    ),
                )
        else:
            card = self._build_result_card(result)
            self.bot.reply_message(message_id, "interactive", card)

    def _on_job_complete(
        self, message_id: str, result: dict, buttons: list[dict],
    ) -> None:
        """Called by AsyncJobPoller when an async job finishes."""
        status = result.get("status", "failed")
        if status == "completed":
            job_result = result.get("result", {})
            text = format_explanation_result(job_result)
        else:
            error = result.get("error", "unknown error")
            text = f"Job failed: {error}"

        card_result = CommandResult(text=text, buttons=buttons)
        card = self._build_result_card(card_result)
        self.bot.update_message(message_id, card)


# ── Lark card helpers ─────────────────────────────────────────────


def _lark_field(content: str, short: bool = False) -> dict:
    return {
        "is_short": short,
        "text": {"tag": "lark_md", "content": content},
    }


def _lark_button(label: str, cmd: str, run_id: str, btn_type: str = "default") -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": btn_type,
        "value": {"cmd": cmd, "run_id": run_id},
    }


# ── HTTP callback handler ────────────────────────────────────────


class _LarkCallbackHandler(BaseHTTPRequestHandler):
    """Handle Lark card action callback POSTs."""

    def __init__(self, *args, channel: LarkCommandChannel, **kwargs):
        self.channel = channel
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        logger.debug("Lark callback: " + format, *args)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""

        # Signature verification (when encrypt_key is configured)
        encrypt_key = self.channel.encrypt_key
        if encrypt_key:
            timestamp = self.headers.get("X-Lark-Request-Timestamp", "")
            nonce = self.headers.get("X-Lark-Request-Nonce", "")
            expected_sig = self.headers.get("X-Lark-Signature", "")
            if not expected_sig:
                self._respond(403, {"error": "missing signature"})
                return
            to_sign = (timestamp + nonce + encrypt_key + body.decode("utf-8")).encode("utf-8")
            computed_sig = hashlib.sha256(to_sign).hexdigest()
            if not hmac.compare_digest(computed_sig, expected_sig):
                logger.warning("Lark callback signature mismatch")
                self._respond(403, {"error": "invalid signature"})
                return

        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        # Verification token check (when configured, for non-challenge requests)
        if self.channel.verification_token:
            token = payload.get("token", "")
            if token and not hmac.compare_digest(token, self.channel.verification_token):
                logger.warning("Lark callback verification token mismatch")
                self._respond(403, {"error": "invalid token"})
                return

        # Lark URL verification challenge
        if "challenge" in payload:
            self._respond(200, {"challenge": payload["challenge"]})
            return

        # Card action callback
        action = payload.get("action", {})
        action_value = action.get("value", {})
        # Extract chat_id and message_id from the event context
        event = payload.get("event", payload)
        chat_id = event.get("open_chat_id", "")
        message_id = event.get("open_message_id", "")

        # Auth check
        if not self.channel.auth.is_authorized(chat_id):
            self._respond(200, {})
            return

        response_card = self.channel.handle_card_action(action_value, chat_id, message_id)
        if response_card:
            # Return card as response to update in-place
            self._respond(200, {"card": response_card})
        else:
            self._respond(200, {})

    def _respond(self, status: int, body: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))
