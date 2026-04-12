from __future__ import annotations

from supervisor.learning import (
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
