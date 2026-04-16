# Operator Channel Host Refactor

**Date:** 2026-04-17
**Status:** Proposed
**Motivation:** Incremental patches (#61, #62, #63) addressed symptoms but not the root cause:
command channels are fundamentally different from notification channels, yet live inside
`NotificationManager`. This causes recurring lifecycle, scope, and contract issues.

---

## Root Cause Analysis

Three structural problems, identified in post-merge review of PRs #61-#63:

### 1. Abstraction Mismatch

`NotificationManager` was designed for stateless, fire-and-forget outbound push (jsonl, tmux
display-message, webhook). Command channels are bidirectional, stateful, long-lived services
with auth, dedup, async jobs, and lifecycle requirements. Bolting them onto a notification
manager creates a leaky abstraction that surfaces as bugs on every PR.

**Evidence:**
- `create_command_channels(config)` + `from_config(command_channels=)` — a two-phase
  construction pattern that exists solely to work around the mismatch
- `start_all()` / `stop_all()` — added to NotificationManager for command channels, but
  notification channels don't need lifecycle management
- Three PRs of lifecycle fixes (#61 fix, #62, #63) all stemmed from this single mismatch

### 2. Undefined Singleton Scope

Command channels are per-daemon singletons (one polling thread, one HTTP port). But the
singleton scope is only enforced by convention, not by the system:

- **Daemon + foreground conflict:** Both `DaemonServer.__init__` and `app.py:run_foreground`
  create command channels from the same config. If both run simultaneously, they fight over
  Telegram update offsets and Lark callback ports.
- **Multi-worktree conflict:** Multiple daemons in different worktrees with the same bot
  config would each start their own polling/HTTP server.
- **No cross-process coordination:** No lock file, no port reservation, no offset
  partitioning.

### 3. Late Canonical Contract

The operator action layer (`RunContext` + `OperatorActions` + `command_dispatch`) was built
correctly, but no contract test verifies that all channels route through it identically. Each
channel can drift silently.

---

## Proposed Changes

### Action 1: Extract `OperatorChannelHost`

Create `supervisor/operator/channel_host.py` with a dedicated class that owns command channel
lifecycle, completely separate from `NotificationManager`.

```python
class OperatorChannelHost:
    """Owns command channel lifecycle: create, start, stop, health check.

    Singleton per process. Separate from NotificationManager.
    """

    def __init__(self, channels: list[CommandChannel]):
        self._channels = channels

    @classmethod
    def from_config(cls, config: RuntimeConfig) -> "OperatorChannelHost":
        """Create command channels from config."""
        # Current logic from NotificationManager.create_command_channels()

    def start(self) -> None:
        """Start all command channels (idempotent)."""

    def stop(self) -> None:
        """Stop all command channels."""

    def notify(self, event: NotificationEvent) -> None:
        """Forward notifications to command channels that also act as alert surfaces."""

    @property
    def channels(self) -> list:
        """Expose channels for NotificationManager integration."""
```

**Migration path:**

1. Extract the logic from `NotificationManager.create_command_channels()` into
   `OperatorChannelHost.from_config()`
2. `DaemonServer.__init__` creates `OperatorChannelHost` instead of raw channel list
3. `app.py:run_foreground` creates `OperatorChannelHost` instead of raw channel list
4. `NotificationManager.from_config()` receives `OperatorChannelHost` (or its channels)
   for notification forwarding only — no lifecycle management
5. Remove `create_command_channels()`, `start_all()`, `stop_all()` from `NotificationManager`
6. `NotificationManager` returns to its original purpose: stateless notification dispatch

**Files changed:**
- NEW: `supervisor/operator/channel_host.py`
- MODIFY: `supervisor/notifications.py` — remove command channel logic
- MODIFY: `supervisor/daemon/server.py` — use `OperatorChannelHost`
- MODIFY: `supervisor/app.py` — use `OperatorChannelHost`

### Action 2: Define Singleton Scope

Add explicit cross-process coordination so only one command channel host binds per
bot-config identity.

**Scope rule:** One `OperatorChannelHost` per unique integration config (bot_token or
app_id+app_secret). Not per-daemon, not per-worktree — per-credential-set.

**Mechanism:** Lock file at `~/.supervisor/locks/{config_hash}.lock` where `config_hash`
is derived from the credential identity (e.g., SHA256 of bot_token or app_id).

```python
class OperatorChannelHost:
    def start(self) -> None:
        for channel in self._channels:
            lock = self._acquire_lock(channel)
            if not lock:
                logger.warning(
                    "skipping %s: another process holds the lock",
                    channel.__class__.__name__,
                )
                continue
            channel.start()
```

**What this prevents:**
- Daemon + foreground both polling same Telegram bot
- Two daemons in different worktrees fighting over same Lark callback port
- Silent data races on update offsets

**Graceful degradation:** When a channel can't acquire its lock, the host logs a warning
and skips it. The process still functions — it just doesn't have IM control. Notifications
still flow through webhook/jsonl channels.

**Files changed:**
- MODIFY: `supervisor/operator/channel_host.py` — add lock acquisition
- NEW: `supervisor/operator/channel_lock.py` — lock file utilities (or inline in host)

### Action 3: Freeze Canonical Operator Contract

Define a `CommandChannel` protocol that all IM adapters must implement:

```python
class CommandChannel(Protocol):
    """Contract for IM operator command channels."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def notify(self, event: NotificationEvent) -> None: ...

    # Identity for singleton locking
    @property
    def config_identity(self) -> str:
        """Unique identity for cross-process singleton coordination.
        E.g., SHA256(bot_token) or SHA256(app_id)."""
        ...
```

The key constraint: **all command routing MUST go through `dispatch_command()`**. No adapter
may call `RunContext` or operator actions directly. This is already true today but is not
enforced by tests.

**Files changed:**
- MODIFY: `supervisor/operator/command_dispatch.py` — add `CommandChannel` protocol
- MODIFY: `supervisor/adapters/telegram_command.py` — declare protocol conformance
- MODIFY: `supervisor/adapters/lark_command.py` — declare protocol conformance

### Action 4: Scenario Contract Tests

Add tests that verify cross-process, cross-channel behavioral contracts:

```python
# tests/test_channel_contract.py

class TestChannelContract:
    """Every CommandChannel implementation routes through dispatch_command."""

    @pytest.mark.parametrize("channel_cls", [TelegramCommandChannel, LarkCommandChannel])
    def test_all_commands_use_dispatch(self, channel_cls):
        """Verify no channel calls operator actions directly."""
        # Static analysis: grep channel source for direct action imports

    def test_singleton_lock_prevents_double_start(self):
        """Two hosts with same config identity: only one starts."""

    def test_different_configs_both_start(self):
        """Two hosts with different credentials: both start."""

    def test_foreground_skips_when_daemon_holds_lock(self):
        """Foreground process gracefully degrades when daemon owns the lock."""

class TestNotificationManagerPurity:
    """NotificationManager has no command channel logic after extraction."""

    def test_no_start_stop_methods(self):
        """start_all/stop_all removed or no-op."""

    def test_no_create_command_channels(self):
        """create_command_channels removed."""

    def test_from_config_ignores_mode_command(self):
        """mode=command entries are not created by NotificationManager."""
```

**Files changed:**
- NEW: `tests/test_channel_contract.py`
- MODIFY: existing test files to remove now-redundant lifecycle tests

---

## Implementation Order

1. **Extract `OperatorChannelHost`** — pure extraction, no behavior change
2. **Update callsites** — `DaemonServer` and `app.py` use new host
3. **Add `CommandChannel` protocol** — type-level contract
4. **Add singleton lock** — cross-process coordination
5. **Add contract tests** — verify invariants
6. **Clean up `NotificationManager`** — remove command channel vestiges

Each step should compile and pass all existing tests.

---

## What This Does NOT Change

- `command_dispatch.py` — shared dispatch layer is correct, no changes needed
- `telegram_command.py` / `lark_command.py` — transport adapters stay as-is
- `NotificationEvent` / notification channels (jsonl, tmux, webhook) — untouched
- Operator action layer (`RunContext`, `OperatorActions`, etc.) — untouched

---

## Verification

1. All existing 691+ tests pass
2. New contract tests verify singleton, dispatch-only routing, and extraction completeness
3. Manual: start daemon, then foreground with same config — foreground logs "skipping, lock
   held" and still works for non-IM operations
