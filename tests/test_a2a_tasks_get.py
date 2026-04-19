"""Tests for tasks/get.

Contract:
- ``handle_tasks_get`` takes ``params={id: <request_id>}`` plus an
  ``EventPlaneStore`` and returns the A2A task status shape:
  ``{id, status: {state}, artifacts: [...]}``.
- ``state`` maps from our ``ExternalTaskRequest.status``:
    pending  -> queued
    in_flight -> in_progress
    completed -> completed
    failed    -> failed
    expired   -> cancelled
- ``artifacts`` lists all results for the request; each becomes a
  ``{type: "text", text: <result.summary>, metadata: <result.payload>}``.
- Unknown request_id raises ``A2AGetError`` (HTTP layer returns JSON-RPC
  error).
"""
from __future__ import annotations

from supervisor.adapters.a2a.task_mapper import A2AGetError, handle_tasks_get
from supervisor.event_plane.ingest import EventPlaneIngest
from supervisor.event_plane.models import ExternalTaskRequest
from supervisor.event_plane.store import EventPlaneStore


def _ingest(tmp_path) -> EventPlaneIngest:
    return EventPlaneIngest(EventPlaneStore(str(tmp_path / "runtime")))


def test_queued_status_after_register(tmp_path):
    ingest = _ingest(tmp_path)
    reg = ingest.register_request(session_id="s1", provider="a2a", target_ref="ref-1")
    out = handle_tasks_get(params={"id": reg["request_id"]}, store=ingest.store)
    assert out["id"] == reg["request_id"]
    assert out["status"]["state"] == "queued"
    assert out["artifacts"] == []


def test_completed_status_after_ingest_result(tmp_path):
    ingest = _ingest(tmp_path)
    reg = ingest.register_request(session_id="s1", provider="a2a", target_ref="ref-1")
    ingest.ingest_result(
        request_id=reg["request_id"],
        provider="a2a",
        result_kind="review_comments",
        summary="LGTM",
        payload={"line_notes": []},
    )
    out = handle_tasks_get(params={"id": reg["request_id"]}, store=ingest.store)
    assert out["status"]["state"] == "completed"
    assert len(out["artifacts"]) == 1
    art = out["artifacts"][0]
    assert art["type"] == "text"
    assert art["text"] == "LGTM"
    assert art["metadata"]["result_kind"] == "review_comments"


def test_in_flight_maps_to_in_progress(tmp_path):
    ingest = _ingest(tmp_path)
    reg = ingest.register_request(session_id="s1", provider="a2a", target_ref="ref-1")
    # Manually flip status to in_flight.
    req = ingest.store.latest_request(reg["request_id"])
    updated = ExternalTaskRequest.from_dict(req.to_dict())
    updated.status = "in_flight"
    ingest.store.append_request(updated)

    out = handle_tasks_get(params={"id": reg["request_id"]}, store=ingest.store)
    assert out["status"]["state"] == "in_progress"


def test_unknown_id_raises(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    try:
        handle_tasks_get(params={"id": "req_missing"}, store=store)
    except A2AGetError as exc:
        assert "req_missing" in str(exc)
    else:
        raise AssertionError("expected A2AGetError")


def test_missing_id_raises(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    try:
        handle_tasks_get(params={}, store=store)
    except A2AGetError as exc:
        assert "id" in str(exc).lower()
    else:
        raise AssertionError("expected A2AGetError")
