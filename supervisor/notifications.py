from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


logger = logging.getLogger(__name__)


@dataclass
class NotificationEvent:
    event_type: str
    run_id: str
    top_state: str
    reason: str
    next_action: str
    pane_target: str = ""
    spec_path: str = ""
    workspace_root: str = ""
    surface_type: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


class NotificationChannel(Protocol):
    def notify(self, event: NotificationEvent) -> None:
        ...


class JsonlNotificationChannel:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def notify(self, event: NotificationEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


class TmuxDisplayNotificationChannel:
    def __init__(self, *, tmux_socket: str | None = None):
        self.tmux_socket = tmux_socket

    def notify(self, event: NotificationEvent) -> None:
        if event.surface_type and event.surface_type != "tmux":
            return
        if not event.pane_target:
            return

        cmd = ["tmux"]
        if self.tmux_socket:
            cmd += ["-S", self.tmux_socket]
        cmd += [
            "display-message",
            "-d",
            str(self._duration_ms(event)),
            "-t",
            event.pane_target,
            self._format_message(event),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            logger.warning("tmux notification failed: %s", result.stderr.strip())

    @staticmethod
    def _format_message(event: NotificationEvent) -> str:
        reason = event.reason or "paused for human"
        action = event.next_action or "check thin-supervisor status"
        return f"[thin-supervisor] {event.top_state}: {reason} | next: {action}"

    @staticmethod
    def _duration_ms(event: NotificationEvent) -> int:
        if event.event_type in {"run_completed", "human_pause"}:
            return 15000
        if event.event_type in {"step_verified", "auto_intervention"}:
            return 8000
        return 4000


class NotificationManager:
    def __init__(self, channels: list[NotificationChannel] | None = None):
        self.channels = channels or []

    @classmethod
    def from_config(cls, config, *, runtime_root: str | Path) -> "NotificationManager":
        runtime_root_path = Path(runtime_root)
        channels: list[NotificationChannel] = []
        for entry in getattr(config, "notification_channels", []) or []:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind", "").strip()
            if kind == "jsonl":
                raw_path = entry.get("path", "notifications.jsonl")
                path = Path(raw_path)
                if not path.is_absolute():
                    path = runtime_root_path / raw_path
                channels.append(JsonlNotificationChannel(path))
            elif kind == "tmux_display":
                channels.append(TmuxDisplayNotificationChannel(tmux_socket=entry.get("tmux_socket")))
            else:
                logger.warning("unknown notification channel kind: %s", kind)
        return cls(channels)

    def notify(self, event: NotificationEvent) -> None:
        for channel in self.channels:
            try:
                channel.notify(event)
            except Exception:
                logger.exception("notification channel failed")
