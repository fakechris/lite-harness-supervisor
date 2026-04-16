"""OperatorChannelHost — owns command channel lifecycle.

Extracts command channel creation, start/stop, and cross-process
singleton coordination out of NotificationManager.  NotificationManager
returns to its original purpose: stateless notification dispatch.

Uses advisory file locking (fcntl.flock) so locks auto-release on
process crash without stale lock cleanup.

Key distinction: the lock controls who starts the *inbound transport*
(Telegram polling, Lark HTTP server).  Outbound notification delivery
(sendMessage) works for ALL processes regardless of lock ownership.
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
        Two channels with the same config_identity must not have their
        inbound transport running concurrently.
        """
        ...


# ── Advisory file locking ────────────────────────────────────────


def _lock_path(config_identity: str) -> Path:
    """Return lock file path for a config identity."""
    return Path(LOCK_DIR) / f"{config_identity}.lock"


def _try_acquire_lock(config_identity: str) -> IO | None:
    """Try to acquire an advisory lock.  Returns file handle if acquired, None otherwise.

    Uses fcntl.flock (LOCK_EX | LOCK_NB) — the OS releases the lock
    automatically if the process crashes.  Opens with O_RDWR|O_CREAT
    to avoid truncating the file before the lock is held.
    """
    path = _lock_path(config_identity)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        fh = os.fdopen(fd, "r+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()}\n")
            fh.flush()
            return fh
        except (OSError, IOError):
            fh.close()
            return None
    except (OSError, IOError):
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

    Separates two concerns:
    - **Transport ownership** (singleton): inbound command receiving via
      polling threads / HTTP servers.  Requires cross-process lock.
    - **Notification delivery** (any process): outbound message sending
      via bot API.  No lock required — all channels can notify().

    This means a non-owner process (lock held by another daemon) can
    still send pause/block/completed alerts through Telegram/Lark.
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
        """Start inbound transports, acquiring cross-process locks.

        Lock only controls who runs the polling thread / HTTP server.
        ALL channels remain available for outbound notifications
        regardless of lock ownership.  Returns self.
        """
        for channel in self._channels:
            identity = channel.config_identity
            if identity in self._locks:
                # Same identity already has transport started in this
                # process — skip starting a duplicate poller/server,
                # but the channel is still in _channels for notify().
                continue
            lock = _try_acquire_lock(identity)
            if not lock:
                logger.warning(
                    "%s: another process owns the transport for %s — "
                    "this process will send notifications but not receive commands",
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
        """Stop all started transports and release locks."""
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
        """Forward notifications to ALL channels, not just transport owners.

        Outbound message delivery (sendMessage) does not require owning
        the inbound transport.  Every process can push alerts.
        """
        for channel in self._channels:
            try:
                channel.notify(event)
            except Exception:
                logger.exception("command channel notification failed")

    @property
    def channels(self) -> list[CommandChannel]:
        """All channels — for NotificationManager notification forwarding.

        Returns all channels (not just transport owners) so that every
        process can deliver alerts through Telegram/Lark.
        """
        return list(self._channels)

    @property
    def transport_owners(self) -> list[CommandChannel]:
        """Channels with active inbound transport (lock acquired)."""
        return list(self._started)


def config_identity_from_token(token: str) -> str:
    """Compute config identity from a bot token or app_id."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
