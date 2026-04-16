"""OperatorChannelHost — owns command channel lifecycle.

Extracts command channel creation, start/stop, and cross-process
singleton coordination out of NotificationManager.  NotificationManager
returns to its original purpose: stateless notification dispatch.

Uses advisory file locking (fcntl.flock) so locks auto-release on
process crash without stale lock cleanup.
"""
from __future__ import annotations

import fcntl
import hashlib
import logging
import os
from pathlib import Path
from typing import IO, Protocol, runtime_checkable

from supervisor.notifications import NotificationEvent

logger = logging.getLogger(__name__)

LOCK_DIR = os.path.expanduser("~/.supervisor/locks")


# ── CommandChannel protocol ──────────────────────────────────────


@runtime_checkable
class CommandChannel(Protocol):
    """Contract for IM operator command channels."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def notify(self, event: NotificationEvent) -> None: ...

    @property
    def config_identity(self) -> str:
        """Unique identity for cross-process singleton coordination.

        Derived from credentials (e.g. SHA256 of bot_token or app_id).
        Two channels with the same config_identity must not run concurrently.
        """
        ...


# ── Advisory file locking ────────────────────────────────────────


def _lock_path(config_identity: str) -> Path:
    """Return lock file path for a config identity."""
    return Path(LOCK_DIR) / f"{config_identity}.lock"


def _try_acquire_lock(config_identity: str) -> IO | None:
    """Try to acquire an advisory lock.  Returns file handle if acquired, None otherwise.

    Uses fcntl.flock (LOCK_EX | LOCK_NB) — the OS releases the lock
    automatically if the process crashes.
    """
    path = _lock_path(config_identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fh = open(path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        return fh
    except (OSError, IOError):
        try:
            fh.close()
        except Exception:
            pass
        return None


def _release_lock(fh: IO) -> None:
    """Release an advisory lock."""
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
    except Exception:
        pass


# ── OperatorChannelHost ──────────────────────────────────────────


class OperatorChannelHost:
    """Owns command channel lifecycle: create, start, stop, health check.

    Singleton per process.  Uses advisory file locking per credential
    set to prevent cross-process conflicts (daemon + foreground, or
    multiple daemons sharing the same bot config).
    """

    def __init__(self, channels: list[CommandChannel]):
        self._channels: list[CommandChannel] = channels
        self._started: list[CommandChannel] = []
        self._locks: dict[str, IO] = {}  # config_identity -> file handle

    @classmethod
    def from_config(cls, config) -> "OperatorChannelHost":
        """Create command channels from config entries with mode=command."""
        channels: list[CommandChannel] = []
        for entry in getattr(config, "notification_channels", []) or []:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind", "").strip()
            mode = entry.get("mode", "notify")
            if mode != "command":
                continue
            if kind == "telegram":
                try:
                    from supervisor.adapters.telegram_command import TelegramCommandChannel
                    channels.append(TelegramCommandChannel(
                        bot_token=entry.get("bot_token", ""),
                        chat_id=entry.get("chat_id", ""),
                        allowed_chat_ids=entry.get("allowed_chat_ids"),
                        allowed_user_ids=entry.get("allowed_user_ids"),
                        language=entry.get("language", "zh"),
                    ))
                except (ValueError, Exception) as exc:
                    logger.warning("skipping telegram command channel: %s", exc)
            elif kind == "lark":
                try:
                    from supervisor.adapters.lark_command import LarkCommandChannel
                    channels.append(LarkCommandChannel(
                        app_id=entry.get("app_id", ""),
                        app_secret=entry.get("app_secret", ""),
                        allowed_chat_ids=entry.get("allowed_chat_ids"),
                        allowed_user_ids=entry.get("allowed_user_ids"),
                        language=entry.get("language", "zh"),
                        callback_port=entry.get("callback_port", 9876),
                        verification_token=entry.get("verification_token", ""),
                        encrypt_key=entry.get("encrypt_key", ""),
                    ))
                except (ValueError, Exception) as exc:
                    logger.warning("skipping lark command channel: %s", exc)
        return cls(channels)

    def start(self) -> "OperatorChannelHost":
        """Start channels, acquiring cross-process locks.  Returns self.

        Channels whose lock is held by another process are skipped with
        a warning — the process still functions, just without IM control.
        """
        for channel in self._channels:
            identity = channel.config_identity
            if identity in self._locks:
                continue  # already started
            lock = _try_acquire_lock(identity)
            if not lock:
                logger.warning(
                    "skipping %s: another process holds the lock for %s",
                    channel.__class__.__name__,
                    identity[:12],
                )
                continue
            try:
                channel.start()
                self._locks[identity] = lock
                self._started.append(channel)
            except Exception:
                logger.exception("failed to start %s", channel.__class__.__name__)
                _release_lock(lock)

        return self

    def stop(self) -> None:
        """Stop all started channels and release locks."""
        for channel in self._started:
            try:
                channel.stop()
            except Exception:
                logger.exception("failed to stop %s", channel.__class__.__name__)
        self._started.clear()
        for identity, lock in self._locks.items():
            _release_lock(lock)
        self._locks.clear()

    def notify(self, event: NotificationEvent) -> None:
        """Forward notifications to started command channels."""
        for channel in self._started:
            try:
                channel.notify(event)
            except Exception:
                logger.exception("command channel notification failed")

    @property
    def channels(self) -> list[CommandChannel]:
        """Expose started channels (for NotificationManager integration)."""
        return list(self._started)


def config_identity_from_token(token: str) -> str:
    """Compute config identity from a bot token or app_id."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
