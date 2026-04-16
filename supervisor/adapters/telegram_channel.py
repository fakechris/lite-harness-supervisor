"""Telegram notification channel adapter.

Sends operator notifications to a Telegram chat via Bot API.
Uses urllib to avoid external dependencies.
"""
from __future__ import annotations

import json
import logging
from urllib import request as urllib_request
from urllib.error import URLError

from supervisor.notifications import NotificationEvent

logger = logging.getLogger(__name__)


class TelegramNotificationChannel:
    """Push notifications to Telegram via Bot API.

    Config:
        notification_channels:
          - kind: telegram
            bot_token: "123456:ABC-..."
            chat_id: "-1001234567890"
    """

    TELEGRAM_API = "https://api.telegram.org"

    def __init__(self, *, bot_token: str, chat_id: str):
        if not bot_token:
            raise ValueError("telegram bot_token is required")
        if not chat_id:
            raise ValueError("telegram chat_id is required")
        self.bot_token = bot_token
        self.chat_id = chat_id

    def notify(self, event: NotificationEvent) -> None:
        message = self._format_message(event)
        self._send_message(message)

    def _format_message(self, event: NotificationEvent) -> str:
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

    def _send_message(self, text: str) -> None:
        url = f"{self.TELEGRAM_API}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_notification": False,
        }
        try:
            req = urllib_request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if not result.get("ok"):
                    logger.warning("Telegram API error: %s", result.get("description", "unknown"))
        except (URLError, OSError) as exc:
            logger.warning("Telegram notification failed: %s", exc)
        except Exception:
            logger.exception("Telegram notification unexpected error")


def _event_emoji(event_type: str) -> str:
    return {
        "human_pause": "🔴",
        "run_completed": "✅",
        "step_verified": "✔️",
        "auto_intervention": "⚠️",
    }.get(event_type, "📋")


def _escape_md(text: str) -> str:
    """Escape MarkdownV2 special characters (including backslash)."""
    special = set(r"_*[]()~`>#+-=|{}.!\\")
    result = []
    for ch in text:
        if ch in special:
            result.append("\\")
        result.append(ch)
    return "".join(result)


def _escape_code(text: str) -> str:
    """Escape text for use inside MarkdownV2 backtick code spans.

    Only backticks and backslashes need escaping inside code spans.
    """
    return text.replace("\\", "\\\\").replace("`", "\\`")
