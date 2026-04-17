"""Telegram command channel — full operator control via Bot API.

Supports text commands (/runs, /inspect, /explain, etc.), inline keyboard
buttons on alert cards, and progressive message editing for async jobs.
Uses urllib only (no external dependencies).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from urllib import request as urllib_request
from urllib.error import URLError

from supervisor.adapters.telegram_channel import _escape_code, _escape_md, _event_emoji
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


class TelegramCommandChannel:
    """Full operator command channel over Telegram Bot API.

    Implements the NotificationChannel protocol (outbound alerts with
    inline keyboard buttons) AND runs a getUpdates polling thread for
    inbound text commands and button callbacks.

    Config::

        notification_channels:
          - kind: telegram
            mode: command
            bot_token: "123456:ABC-..."
            chat_id: "-100xxx"
            allowed_chat_ids: ["-100xxx"]
            language: "zh"
    """

    TELEGRAM_API = "https://api.telegram.org"

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        allowed_chat_ids: list[str] | None = None,
        allowed_user_ids: list[str] | None = None,
        language: str = "zh",
    ):
        if not bot_token:
            raise ValueError("telegram bot_token is required")
        if not chat_id:
            raise ValueError("telegram chat_id is required")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.language = language
        self.auth = CommandAuth(
            allowed_chat_ids=allowed_chat_ids or [chat_id],
            allowed_user_ids=allowed_user_ids,
        )
        self._update_offset = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._poller = AsyncJobPoller()

    @property
    def config_identity(self) -> str:
        """Unique identity for cross-process singleton coordination."""
        from supervisor.operator.channel_host import config_identity_from_token
        return config_identity_from_token(self.bot_token)

    # ── NotificationChannel protocol ──────────────────────────────

    def notify(self, event: NotificationEvent) -> None:
        """Send alert with inline keyboard buttons."""
        text = self._format_alert(event)
        keyboard = self._build_alert_keyboard(event.run_id)
        self._send_message(self.chat_id, text, reply_markup=keyboard)

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        """Start getUpdates polling thread and job poller."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._poller.start()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Telegram command channel started")

    def stop(self) -> None:
        self._stop_event.set()
        self._poller.stop()

    # ── Inbound polling ───────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Long-poll getUpdates, dispatch commands."""
        while not self._stop_event.is_set():
            try:
                updates = self._get_updates(
                    offset=self._update_offset, timeout=30,
                )
                for update in updates:
                    update_id = update.get("update_id", 0)
                    if update_id >= self._update_offset:
                        self._update_offset = update_id + 1
                    # Skip old text messages on startup (not callbacks —
                    # callback_query.message.date is the *alert* send time,
                    # not the button click time, so filtering would break
                    # buttons pressed >60s after the alert was sent).
                    if "message" in update and "callback_query" not in update:
                        msg_date = update["message"].get("date", 0)
                        if msg_date and time.time() - msg_date > 60:
                            continue
                    if "message" in update:
                        self._handle_message(update["message"])
                    elif "callback_query" in update:
                        self._handle_callback(update["callback_query"])
            except Exception:
                logger.exception("Telegram poll error")
                self._stop_event.wait(5)

    def _handle_message(self, message: dict) -> None:
        """Handle a text message (command)."""
        chat_id = str(message.get("chat", {}).get("id", ""))
        user_id = str(message.get("from", {}).get("id", ""))
        text = message.get("text", "")
        if not text:
            return
        if not self.auth.is_authorized(chat_id, user_id):
            return  # fail-closed
        cmd, args = parse_command(text)
        if not cmd:
            return
        result = dispatch_command(cmd, args, language=self.language)
        self._send_result(chat_id, result)

    def _handle_callback(self, callback_query: dict) -> None:
        """Handle inline keyboard button press."""
        chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
        user_id = str(callback_query.get("from", {}).get("id", ""))
        callback_id = callback_query.get("id", "")

        if not self.auth.is_authorized(chat_id, user_id):
            self._answer_callback(callback_id, "Unauthorized")
            return

        try:
            data = json.loads(callback_query.get("data", "{}"))
        except json.JSONDecodeError:
            self._answer_callback(callback_id, "Invalid callback")
            return

        cmd = data.get("c", "")
        run_id = data.get("r", "")
        if not cmd:
            self._answer_callback(callback_id)
            return

        self._answer_callback(callback_id, cmd.capitalize())
        result = dispatch_command(cmd, [run_id] if run_id else [], language=self.language)
        msg_id = callback_query.get("message", {}).get("message_id")
        if msg_id and not result.job:
            # Sync result: edit the original message, preserving action buttons
            keyboard = self._build_result_keyboard(result.buttons) if result.buttons else None
            self._edit_message(chat_id, msg_id, self._format_text_result(result), reply_markup=keyboard)
        else:
            # Async or no msg_id: send new message
            self._send_result(chat_id, result)

    # ── Result delivery ───────────────────────────────────────────

    def _send_result(self, chat_id: str, result: CommandResult) -> None:
        """Send a command result, with async job tracking if needed."""
        text = self._format_text_result(result)
        keyboard = self._build_result_keyboard(result.buttons) if result.buttons else None

        if result.job and result.ctx:
            # Async: send "Working..." and track the job
            resp = self._send_message(chat_id, text, reply_markup=keyboard)
            msg_id = resp.get("result", {}).get("message_id") if resp else None
            if msg_id:
                self._poller.track(
                    result.ctx,
                    result.job,
                    lambda r, cid=chat_id, mid=msg_id, kb=keyboard: self._on_job_complete(
                        cid, mid, r, kb,
                    ),
                )
        else:
            self._send_message(chat_id, text, reply_markup=keyboard)

    def _on_job_complete(
        self, chat_id: str, message_id: int, result: dict, keyboard: dict | None,
    ) -> None:
        """Called by AsyncJobPoller when an async job finishes."""
        status = result.get("status", "failed")
        if status == "completed":
            job_result = result.get("result", {})
            text = format_explanation_result(job_result)
        else:
            error = result.get("error", "unknown error")
            text = f"Job failed: {error}"
        # Escape for MarkdownV2
        escaped = _escape_md(text)
        self._edit_message(chat_id, message_id, escaped, reply_markup=keyboard)

    # ── Formatting ────────────────────────────────────────────────

    def _format_alert(self, event: NotificationEvent) -> str:
        """Format a notification event as Telegram MarkdownV2."""
        emoji = _event_emoji(event.event_type)
        lines = [
            f"{emoji} *\\[thin\\-supervisor\\]* {_escape_md(event.top_state)}",
            f"Run: `{_escape_code(event.run_id)}`",
        ]
        if event.reason:
            lines.append(f"Reason: {_escape_md(event.reason)}")
        if event.next_action:
            lines.append(f"Next: `{_escape_code(event.next_action)}`")
        if event.workspace_root:
            lines.append(f"Worktree: `{_escape_code(event.workspace_root)}`")
        return "\n".join(lines)

    def _format_text_result(self, result: CommandResult) -> str:
        """Format a CommandResult as MarkdownV2 text."""
        if result.job:
            return "Working\\.\\.\\."
        return _escape_md(result.text)

    def _build_alert_keyboard(self, run_id: str) -> dict:
        """Build inline keyboard for alert messages."""
        short_id = run_id[-12:] if len(run_id) > 12 else run_id
        return {
            "inline_keyboard": [
                [
                    self._cb_button("Inspect", "inspect", short_id),
                    self._cb_button("Explain", "explain", short_id),
                    self._cb_button("Drift", "drift", short_id),
                ],
                [
                    self._cb_button("Pause", "pause", short_id),
                    self._cb_button("Resume", "resume", short_id),
                    self._cb_button("Notes", "notes", short_id),
                ],
            ],
        }

    def _build_result_keyboard(self, buttons: list[dict[str, str]]) -> dict | None:
        """Build inline keyboard from CommandResult buttons."""
        if not buttons:
            return None
        row: list[dict] = []
        rows: list[list[dict]] = []
        for b in buttons:
            short_id = b["run_id"][-12:] if len(b.get("run_id", "")) > 12 else b.get("run_id", "")
            row.append(self._cb_button(b["label"], b["cmd"], short_id))
            if len(row) >= 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return {"inline_keyboard": rows}

    @staticmethod
    def _cb_button(label: str, cmd: str, run_id: str) -> dict:
        """Build a single inline keyboard button with compact callback data."""
        return {
            "text": label,
            "callback_data": json.dumps({"c": cmd, "r": run_id}, separators=(",", ":")),
        }

    # ── Telegram Bot API calls ────────────────────────────────────

    def _api_call(self, method: str, payload: dict) -> dict | None:
        """Call a Telegram Bot API method.  Returns parsed response or None."""
        url = f"{self.TELEGRAM_API}/bot{self.bot_token}/{method}"
        try:
            req = urllib_request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=35) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, OSError) as exc:
            logger.warning("Telegram API %s failed: %s", method, exc)
        except Exception:
            logger.exception("Telegram API %s unexpected error", method)
        return None

    def _get_updates(self, offset: int = 0, timeout: int = 30) -> list[dict]:
        """Long-poll for updates."""
        payload: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset:
            payload["offset"] = offset
        result = self._api_call("getUpdates", payload)
        if result and result.get("ok"):
            return result.get("result", [])
        return []

    def _send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_markup: dict | None = None,
        parse_mode: str = "MarkdownV2",
    ) -> dict | None:
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._api_call("sendMessage", payload)

    def _edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        *,
        reply_markup: dict | None = None,
        parse_mode: str = "MarkdownV2",
    ) -> dict | None:
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._api_call("editMessageText", payload)

    def _answer_callback(self, callback_query_id: str, text: str = "") -> None:
        payload: dict = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._api_call("answerCallbackQuery", payload)
