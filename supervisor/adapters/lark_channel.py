"""Lark/Feishu notification channel adapter.

Sends operator notifications to Lark via incoming webhook.
Uses urllib to avoid external dependencies.
"""
from __future__ import annotations

import json
import logging
from urllib import request as urllib_request
from urllib.error import URLError

from supervisor.notifications import NotificationEvent

logger = logging.getLogger(__name__)


class LarkNotificationChannel:
    """Push notifications to Lark/Feishu via webhook.

    Config:
        notification_channels:
          - kind: lark
            webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
    """

    def __init__(self, *, webhook_url: str):
        if not webhook_url:
            raise ValueError("lark webhook_url is required")
        self.webhook_url = webhook_url

    def notify(self, event: NotificationEvent) -> None:
        card = self._build_card(event)
        self._post_webhook(card)

    def _build_card(self, event: NotificationEvent) -> dict:
        """Build a Lark interactive message card."""
        emoji = _event_emoji(event.event_type)
        color = _event_color(event.event_type)

        fields = [
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**Run:** `{event.run_id}`"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**State:** {event.top_state}"}},
        ]
        if event.reason:
            fields.append({
                "is_short": False,
                "text": {"tag": "lark_md", "content": f"**Reason:** {event.reason}"},
            })
        if event.next_action:
            fields.append({
                "is_short": False,
                "text": {"tag": "lark_md", "content": f"**Next:** `{event.next_action}`"},
            })
        if event.workspace_root:
            fields.append({
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**Worktree:** `{event.workspace_root}`"},
            })

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"{emoji} [thin-supervisor] {event.top_state}",
                    },
                    "template": color,
                },
                "elements": [
                    {
                        "tag": "div",
                        "fields": fields,
                    },
                ],
            },
        }

    def _post_webhook(self, payload: dict) -> None:
        try:
            req = urllib_request.Request(
                self.webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                code = result.get("code", result.get("StatusCode", -1))
                if code != 0:
                    logger.warning("Lark webhook error: %s", result.get("msg", result.get("StatusMessage", "unknown")))
        except (URLError, OSError) as exc:
            logger.warning("Lark notification failed: %s", exc)
        except Exception:
            logger.exception("Lark notification unexpected error")


def _event_emoji(event_type: str) -> str:
    return {
        "human_pause": "🔴",
        "run_completed": "✅",
        "step_verified": "✔️",
        "auto_intervention": "⚠️",
    }.get(event_type, "📋")


def _event_color(event_type: str) -> str:
    """Lark card header color template."""
    return {
        "human_pause": "red",
        "run_completed": "green",
        "step_verified": "blue",
        "auto_intervention": "orange",
    }.get(event_type, "blue")
