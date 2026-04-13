from __future__ import annotations

import json

from supervisor.config import RuntimeConfig
from supervisor.notifications import (
    JsonlNotificationChannel,
    NotificationEvent,
    NotificationManager,
    TmuxDisplayNotificationChannel,
)


def test_runtime_config_loads_notification_channels(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "notification_channels:\n"
        "  - kind: jsonl\n"
        "    path: .supervisor/runtime/custom-notifications.jsonl\n"
        "  - kind: tmux_display\n"
    )

    config = RuntimeConfig.load(config_path)

    assert len(config.notification_channels) == 2
    assert config.notification_channels[0]["kind"] == "jsonl"
    assert config.notification_channels[1]["kind"] == "tmux_display"


def test_jsonl_notification_channel_appends_stable_records(tmp_path):
    path = tmp_path / "notifications.jsonl"
    channel = JsonlNotificationChannel(path)

    channel.notify(NotificationEvent(
        event_type="human_pause",
        run_id="run_123",
        top_state="PAUSED_FOR_HUMAN",
        reason="node mismatch persisted for 5 checkpoints",
        next_action="thin-supervisor run resume --spec plan.yaml --pane %0 --surface tmux",
        pane_target="%0",
        spec_path="plan.yaml",
        workspace_root="/tmp/workspace",
    ))

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["event_type"] == "human_pause"
    assert records[0]["reason"] == "node mismatch persisted for 5 checkpoints"
    assert records[0]["next_action"].startswith("thin-supervisor run resume")


def test_tmux_display_notification_channel_emits_display_message(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    monkeypatch.setattr("supervisor.notifications.subprocess.run", _fake_run)
    channel = TmuxDisplayNotificationChannel()

    channel.notify(NotificationEvent(
        event_type="human_pause",
        run_id="run_123",
        top_state="PAUSED_FOR_HUMAN",
        reason="checkpoint says blocked",
        next_action="thin-supervisor run resume --spec plan.yaml --pane %0 --surface tmux",
        pane_target="%0",
        spec_path="plan.yaml",
        workspace_root="/tmp/workspace",
    ))

    assert calls
    assert calls[0][:2] == ["tmux", "display-message"]
    assert "-d" in calls[0]
    assert "-t" in calls[0]
    assert "%0" in calls[0]
    assert "checkpoint says blocked" in calls[0][-1]


def test_tmux_display_notification_channel_uses_persistent_duration_for_completion(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    monkeypatch.setattr("supervisor.notifications.subprocess.run", _fake_run)
    channel = TmuxDisplayNotificationChannel()

    channel.notify(NotificationEvent(
        event_type="run_completed",
        run_id="run_123",
        top_state="COMPLETED",
        reason="workflow completed",
        next_action="thin-supervisor run summarize run_123",
        pane_target="%0",
        spec_path="plan.yaml",
        workspace_root="/tmp/workspace",
        surface_type="tmux",
    ))

    assert calls
    assert calls[0][:2] == ["tmux", "display-message"]
    assert "-d" in calls[0]
    duration_index = calls[0].index("-d")
    assert int(calls[0][duration_index + 1]) >= 10000
    assert "COMPLETED" in calls[0][-1]
    assert "workflow completed" in calls[0][-1]


def test_notification_manager_builds_channels_from_config(tmp_path):
    config = RuntimeConfig(
        notification_channels=[
            {"kind": "jsonl", "path": str(tmp_path / "notifications.jsonl")},
            {"kind": "tmux_display"},
        ]
    )

    manager = NotificationManager.from_config(config, runtime_root=tmp_path)

    assert len(manager.channels) == 2
