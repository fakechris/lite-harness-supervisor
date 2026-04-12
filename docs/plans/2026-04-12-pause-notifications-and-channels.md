# Pause Notifications And Channels Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `PAUSED_FOR_HUMAN` visible and actionable by surfacing pause reason/next action, notifying the supervised pane, and introducing a pluggable notification-channel interface for future Feishu/Telegram delivery.

**Architecture:** Add a small pause-summary layer that derives stable user-facing metadata from `SupervisorState`, then reuse it in the loop, daemon status/list output, and notification dispatch. Introduce a notification manager with a minimal channel interface and ship two built-in channels: a tmux display-message channel for immediate operator visibility and a JSONL channel for durable notification audit logs.

**Tech Stack:** Python dataclasses, existing daemon/loop/state-store architecture, tmux CLI integration, pytest.

### Task 1: Define pause-summary helpers

**Files:**
- Create: `supervisor/pause_summary.py`
- Modify: `supervisor/domain/models.py`
- Test: `tests/test_pause_summary.py`

**Step 1: Write the failing test**

Cover:
- latest pause reason is derived from `human_escalations`
- default next action for resumable paused runs becomes `thin-supervisor run resume --spec ... --pane ...`
- reviewer-gated pauses become `thin-supervisor run review <run_id> --by human`

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_pause_summary.py`
Expected: FAIL because helper module does not exist.

**Step 3: Write minimal implementation**

Add a helper that accepts a serialized state dict and returns:
- `pause_reason`
- `next_action`
- `is_waiting_for_review`

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_pause_summary.py`
Expected: PASS

### Task 2: Add notification channels and notifier

**Files:**
- Create: `supervisor/notifications.py`
- Modify: `supervisor/config.py`
- Test: `tests/test_notifications.py`

**Step 1: Write the failing test**

Cover:
- config loads `notification_channels`
- tmux-display channel renders pause message command without crashing
- jsonl channel appends stable notification records

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_notifications.py`
Expected: FAIL because channel module/config field is missing.

**Step 3: Write minimal implementation**

Create:
- `NotificationEvent`
- `NotificationChannel` protocol/base
- `JsonlNotificationChannel`
- `TmuxDisplayNotificationChannel`
- `NotificationManager.from_config(...)`

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_notifications.py`
Expected: PASS

### Task 3: Wire pause notifications into the sidecar loop

**Files:**
- Modify: `supervisor/loop.py`
- Test: `tests/test_sidecar_loop.py`
- Test: `tests/test_injection_diagnostics.py`

**Step 1: Write the failing test**

Cover:
- blocked / mismatch / finish-gate pauses emit a notification event
- session log records a `human_pause` event with reason and next action

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_sidecar_loop.py tests/test_injection_diagnostics.py -k human_pause`
Expected: FAIL because no notification/session event exists.

**Step 3: Write minimal implementation**

Add a single helper in the loop to:
- set `PAUSED_FOR_HUMAN`
- append escalation payload
- append `human_pause` session event
- dispatch notification channels

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_sidecar_loop.py tests/test_injection_diagnostics.py -k human_pause`
Expected: PASS

### Task 4: Surface actionable pause metadata in CLI and daemon summaries

**Files:**
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/app.py`
- Test: `tests/test_daemon.py`
- Test: `tests/test_app_cli.py`

**Step 1: Write the failing test**

Cover:
- daemon `status`/`list_runs` include `pause_reason` and `next_action`
- local-state hint output prints reason and next action for paused runs

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_daemon.py tests/test_app_cli.py -k pause`
Expected: FAIL because summaries do not expose those fields.

**Step 3: Write minimal implementation**

Reuse pause-summary helper in server/app rendering logic.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_daemon.py tests/test_app_cli.py -k pause`
Expected: PASS

### Task 5: Document daemon-mode notification behavior and channel extension points

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/ARCHITECTURE.md`

**Step 1: Update docs**

Document:
- what `PAUSED_FOR_HUMAN` means
- where reason/next-action appear
- default notification channels
- how future channels like Feishu/Telegram plug in via `notification_channels`

**Step 2: Verify docs references**

Run: `rg -n "notification_channels|PAUSED_FOR_HUMAN|next_action|tmux display" README.md docs/getting-started.md docs/ARCHITECTURE.md`
Expected: matching lines in all updated docs

### Task 6: Run verification

**Files:**
- Test only

**Step 1: Run targeted tests**

Run: `pytest -q tests/test_pause_summary.py tests/test_notifications.py tests/test_sidecar_loop.py tests/test_injection_diagnostics.py tests/test_daemon.py tests/test_app_cli.py`
Expected: PASS

**Step 2: Run full suite**

Run: `pytest -q`
Expected: PASS
