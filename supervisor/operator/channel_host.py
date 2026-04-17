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

    Filesystem errors (permission, ENOSPC, bad HOME) are logged so they
    can be distinguished from legitimate lock contention during on-call
    debugging, instead of masquerading as "another process owns it".
    """
    path = _lock_path(config_identity)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        fh = os.fdopen(fd, "r+")
    except (OSError, IOError) as exc:
        logger.warning("could not prepare lock file %s: %s", path, exc)
        return None
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


def _release_lock(fh: IO) -> None:
    """Release an advisory lock."""
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
    except Exception:
        pass


# ── Provider Instance merge helpers ──────────────────────────────


def _merge_additive_fields(
    entries: list[dict],
) -> tuple[list[str], list[str], list[str]]:
    """Union conversation targets, allowed_chat_ids, allowed_user_ids.

    Legacy scalar `chat_id` and list `chat_ids` both feed the target set.
    When a group lists only `allowed_chat_ids` (no `chat_id`/`chat_ids`),
    the allowlist values serve as conversation targets too — that matches
    how single-entry configs behaved before the merge contract.

    **Per-entry allowlist promotion.** A single-entry legacy shape
    (e.g. `{chat_id: X}` with no `allowed_chat_ids`) means "X receives
    alerts AND may issue commands" — that was the pre-merge default.
    When such a legacy entry is merged with another entry that *does*
    set `allowed_chat_ids`, the aggregate allowlist becomes non-empty,
    which would silently drop the legacy chat's command authorization
    (it'd still get alerts but `/pause`, `/ask`, etc. would be rejected).
    To preserve intent, an entry that contributes chat_id/chat_ids but
    does not specify its own `allowed_chat_ids` promotes those chats
    into the merged allowlist.  Conversely, an entry that explicitly
    lists `allowed_chat_ids` keeps its narrowing intent — its targets
    are NOT auto-promoted.
    """
    targets: set[str] = set()
    allow_chats: set[str] = set()
    allow_users: set[str] = set()
    for e in entries:
        entry_targets: set[str] = set()
        chat_id = e.get("chat_id")
        if chat_id:
            entry_targets.add(str(chat_id))
        for t in e.get("chat_ids", []) or []:
            if t:
                entry_targets.add(str(t))
        targets |= entry_targets

        if "allowed_chat_ids" in e:
            # Explicit allowlist declared — honor the narrowing intent,
            # even if the list is empty ("this entry authorizes nobody").
            # Do NOT auto-promote this entry's chat_id/chat_ids.
            for c in (e.get("allowed_chat_ids") or []):
                if c:
                    allow_chats.add(str(c))
        else:
            # Legacy shape: key absent → this entry's chats are
            # implicitly authorized (preserve single-entry semantics
            # across the merge).
            allow_chats |= entry_targets

        for u in e.get("allowed_user_ids", []) or []:
            if u:
                allow_users.add(str(u))
    if not targets:
        targets = set(allow_chats)
    return sorted(targets), sorted(allow_chats), sorted(allow_users)


def _require_match(entries: list[dict], field: str, *, default, label: str) -> None:
    """Raise ValueError when `field` disagrees across a Provider Instance group.

    Transport-critical fields (language, callback_port, credentials)
    must not silently diverge — picking one would make behavior depend
    on config entry order, which violates the merge contract.
    """
    seen: set = set()
    for e in entries:
        seen.add(e.get(field, default))
    if len(seen) > 1:
        raise ValueError(
            f"{label} provider instance has conflicting {field!r} values "
            f"across merged config entries: {sorted(str(v) for v in seen)}. "
            "Transport-critical fields must match exactly."
        )


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
        """Build one adapter per Provider Instance from config.

        Multiple `mode: command` entries that share a Provider Instance
        key (Telegram `bot_token`; Lark `app_id`) merge into a single
        adapter:

        - additive fields (conversation targets, allowed_chat_ids,
          allowed_user_ids) are set-unioned;
        - transport-critical fields (language; Lark app_secret,
          callback_port, verification_token, encrypt_key) must match
          exactly across entries — any disagreement raises ValueError
          so the daemon fails closed at startup instead of silently
          picking one value.

        See docs/plans/2026-04-17-im-command-channel-identity-and-merge-semantics.md.
        """
        entries = getattr(config, "notification_channels", []) or []
        command_entries = [
            e for e in entries
            if isinstance(e, dict)
            and e.get("mode", "notify") == "command"
            and e.get("kind", "").strip() in ("telegram", "lark")
        ]

        channels: list[CommandChannel] = []
        channels.extend(cls._build_telegram_channels(command_entries))
        channels.extend(cls._build_lark_channels(command_entries))
        return cls(channels)

    @staticmethod
    def _build_telegram_channels(entries: list[dict]) -> list[CommandChannel]:
        from supervisor.adapters.telegram_command import TelegramCommandChannel

        groups: dict[str, list[dict]] = {}
        for e in entries:
            if e.get("kind") != "telegram":
                continue
            token = e.get("bot_token", "").strip()
            if not token:
                continue
            groups.setdefault(token, []).append(e)

        built: list[CommandChannel] = []
        for token, group in groups.items():
            _require_match(group, "language", default="zh", label="telegram")
            language = group[0].get("language", "zh")
            targets, allow_chats, allow_users = _merge_additive_fields(group)
            built.append(TelegramCommandChannel(
                bot_token=token,
                conversation_targets=targets,
                allowed_chat_ids=allow_chats,
                allowed_user_ids=allow_users,
                language=language,
            ))
        return built

    @staticmethod
    def _build_lark_channels(entries: list[dict]) -> list[CommandChannel]:
        from supervisor.adapters.lark_command import LarkCommandChannel

        groups: dict[str, list[dict]] = {}
        for e in entries:
            if e.get("kind") != "lark":
                continue
            app_id = e.get("app_id", "").strip()
            if not app_id:
                continue
            groups.setdefault(app_id, []).append(e)

        built: list[CommandChannel] = []
        for app_id, group in groups.items():
            _require_match(group, "app_secret", default="", label="lark")
            _require_match(group, "language", default="zh", label="lark")
            _require_match(group, "callback_port", default=9876, label="lark")
            _require_match(group, "verification_token", default="", label="lark")
            _require_match(group, "encrypt_key", default="", label="lark")
            app_secret = group[0].get("app_secret", "").strip()
            if not app_secret:
                # Fail-closed: missing credentials would otherwise be
                # swallowed as "required" ValueError and the misconfigured
                # provider would silently disappear at startup.
                raise ValueError(
                    f"lark provider instance {app_id!r} has empty app_secret "
                    "across all merged config entries; app_secret is required."
                )
            targets, allow_chats, allow_users = _merge_additive_fields(group)
            built.append(LarkCommandChannel(
                app_id=app_id,
                app_secret=app_secret,
                conversation_targets=targets,
                allowed_chat_ids=allow_chats,
                allowed_user_ids=allow_users,
                language=group[0].get("language", "zh"),
                callback_port=group[0].get("callback_port", 9876),
                verification_token=group[0].get("verification_token", ""),
                encrypt_key=group[0].get("encrypt_key", ""),
            ))
        return built

    def start(self) -> "OperatorChannelHost":
        """Start inbound transports, acquiring cross-process locks.

        Each channel corresponds to one Provider Instance (merge has
        already happened in from_config), so there is at most one
        adapter per identity in `self._channels`.  The lock only
        controls who runs the polling thread / HTTP server; every
        channel remains available for outbound notifications
        regardless of lock ownership.  Returns self.
        """
        for channel in self._channels:
            identity = channel.config_identity
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
        for lock in self._locks.values():
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
