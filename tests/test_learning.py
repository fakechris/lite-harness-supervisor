from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.learning import (
    _prefs_path,
    append_friction_event,
    list_friction_events,
    load_user_preferences,
    save_user_preferences,
)


def test_append_and_list_friction_events(tmp_path):
    runtime_dir = tmp_path / ".supervisor" / "runtime"

    first = append_friction_event(
        runtime_dir,
        kind="repeated_confirmation",
        message="user already approved but agent asked again",
        run_id="run_1",
        signals=["user_repeated_approval", "agent_reasked"],
    )
    second = append_friction_event(
        runtime_dir,
        kind="unexpected_pause_confusion",
        message="user did not realize the run was paused",
        run_id="run_2",
        signals=["silent_pause"],
    )

    assert first["event_id"].startswith("friction_")
    assert first["timestamp"]
    assert second["event_id"] != first["event_id"]

    all_events = list_friction_events(runtime_dir)
    assert [event["event_id"] for event in all_events] == [first["event_id"], second["event_id"]]

    run_filtered = list_friction_events(runtime_dir, run_id="run_1")
    assert len(run_filtered) == 1
    assert run_filtered[0]["kind"] == "repeated_confirmation"

    kind_filtered = list_friction_events(runtime_dir, kind="unexpected_pause_confusion")
    assert len(kind_filtered) == 1
    assert kind_filtered[0]["run_id"] == "run_2"


def test_user_preferences_merge_updates(tmp_path):
    runtime_dir = tmp_path / ".supervisor" / "runtime"

    save_user_preferences(runtime_dir, {"approval_style": "terse"})
    save_user_preferences(runtime_dir, {"clarify_tolerance": "low"})

    prefs = load_user_preferences(runtime_dir)

    assert prefs["approval_style"] == "terse"
    assert prefs["clarify_tolerance"] == "low"


def test_load_user_preferences_quarantines_corrupt_store(tmp_path):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    path = _prefs_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="corrupt user preferences store"):
        load_user_preferences(runtime_dir)

    quarantined = list(path.parent.glob("user_preferences.json.corrupt-*"))
    assert len(quarantined) == 1
    assert not path.exists()


def test_save_user_preferences_rejects_corrupt_store_without_overwriting(tmp_path):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    path = _prefs_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="corrupt user preferences store"):
        save_user_preferences(runtime_dir, {"approval_style": "terse"})

    quarantined = list(path.parent.glob("user_preferences.json.corrupt-*"))
    assert len(quarantined) == 1
    assert not path.exists()


def test_save_user_preferences_preserves_other_users(tmp_path):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    path = _prefs_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "default": {"approval_style": "terse"},
                "alice": {"clarify_tolerance": "low"},
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    updated = save_user_preferences(runtime_dir, {"approval_style": "verbose"}, user_id="default")

    assert updated["approval_style"] == "verbose"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["alice"]["clarify_tolerance"] == "low"
