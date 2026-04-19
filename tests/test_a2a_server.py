"""End-to-end tests for the A2A HTTP server.

Spins up ``A2AServer`` bound to 127.0.0.1:0 (kernel-assigned port) and
hits it with ``http.client``. Verifies:

- ``GET /.well-known/agent.json`` returns the card
- ``POST /`` with ``tasks/send`` stores request + mailbox and returns task_id
- ``POST /`` with ``tasks/get`` returns A2A-shaped status
- Bad JSON / bad method → JSON-RPC error frames
- Auth enforcement when token configured
"""
from __future__ import annotations

import http.client
import json
import threading

from supervisor.adapters.a2a.server import A2AServer
from supervisor.boundary.models import InboundGuardConfig
from supervisor.event_plane.ingest import EventPlaneIngest
from supervisor.event_plane.store import EventPlaneStore


def _start_server(
    tmp_path,
    *,
    guard_config: InboundGuardConfig | None = None,
    seed_sessions: tuple[str, ...] = ("s1",),
) -> tuple[A2AServer, int, EventPlaneIngest, threading.Thread]:
    runtime_root = tmp_path / "runtime"
    store = EventPlaneStore(str(runtime_root))
    ingest = EventPlaneIngest(store)
    # Seed sessions so tasks/send passes the existence check.  Each test
    # that wants a specific session to look "unknown" can override
    # ``seed_sessions``.
    shared = runtime_root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    with (shared / "sessions.jsonl").open("a", encoding="utf-8") as f:
        for sid in seed_sessions:
            f.write(json.dumps({"session_id": sid, "status": "active"}) + "\n")
    cfg = guard_config or InboundGuardConfig(
        enable_auth=False, audit_path=tmp_path / "audit.jsonl"
    )
    server = A2AServer(host="127.0.0.1", port=0, ingest=ingest, guard_config=cfg)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.port, ingest, thread


def _stop(server: A2AServer, thread: threading.Thread) -> None:
    server.shutdown()
    thread.join(timeout=2.0)
    server.server_close()


def _http(port: int) -> http.client.HTTPConnection:
    return http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)


def test_agent_card_endpoint(tmp_path):
    server, port, _, thread = _start_server(tmp_path)
    try:
        conn = _http(port)
        conn.request("GET", "/.well-known/agent.json")
        resp = conn.getresponse()
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body["name"] == "thin-supervisor"
        assert body["authentication"]["required"] is False
        conn.close()
    finally:
        _stop(server, thread)


def test_tasks_send_round_trip(tmp_path):
    server, port, ingest, thread = _start_server(tmp_path)
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/send",
            "params": {
                "session_id": "s1",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "review PR 42"}],
                },
            },
        }
        conn = _http(port)
        conn.request(
            "POST", "/",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body["jsonrpc"] == "2.0"
        assert body["id"] == "1"
        request_id = body["result"]["id"]
        assert request_id.startswith("req_")
        # Durability: persisted to store.
        assert ingest.store.latest_request(request_id) is not None
        conn.close()
    finally:
        _stop(server, thread)


def test_tasks_get_round_trip(tmp_path):
    server, port, ingest, thread = _start_server(tmp_path)
    try:
        reg = ingest.register_request(session_id="s1", provider="a2a", target_ref="ref-1")
        payload = {
            "jsonrpc": "2.0", "id": 2, "method": "tasks/get",
            "params": {"id": reg["request_id"]},
        }
        conn = _http(port)
        conn.request(
            "POST", "/",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body["result"]["status"]["state"] == "queued"
        conn.close()
    finally:
        _stop(server, thread)


def test_invalid_json_returns_parse_error(tmp_path):
    server, port, _, thread = _start_server(tmp_path)
    try:
        conn = _http(port)
        conn.request("POST", "/", body=b"not json", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 400
        body = json.loads(resp.read())
        assert "error" in body
        assert body["error"]["code"] == -32700
        conn.close()
    finally:
        _stop(server, thread)


def test_unknown_method_returns_method_not_found(tmp_path):
    server, port, _, thread = _start_server(tmp_path)
    try:
        payload = {"jsonrpc": "2.0", "id": 3, "method": "tasks/nope"}
        conn = _http(port)
        conn.request(
            "POST", "/",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200  # JSON-RPC errors still use 200 by convention
        body = json.loads(resp.read())
        assert body["error"]["code"] == -32601
        conn.close()
    finally:
        _stop(server, thread)


def test_unknown_method_still_runs_the_guard(tmp_path):
    """An attacker probing unknown method names must not bypass auth /
    audit / rate-limit.  Previously the guard lived inside the tasks/*
    branches so unknown methods fell through ungated."""
    cfg = InboundGuardConfig(auth_token="s3cret", audit_path=tmp_path / "audit.jsonl")
    server, port, _, thread = _start_server(tmp_path, guard_config=cfg)
    try:
        payload = {"jsonrpc": "2.0", "id": 9, "method": "tasks/nope", "params": {}}
        conn = _http(port)
        conn.request(
            "POST", "/",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},  # no Authorization
        )
        body = json.loads(conn.getresponse().read())
        # Guard fires BEFORE the method-not-found branch, so the error we
        # see is the guard rejection (A2A_GUARD_REJECT = -32003), not
        # METHOD_NOT_FOUND (-32601).
        assert body["error"]["code"] == -32003
        conn.close()
    finally:
        _stop(server, thread)

    # Audit line recorded for the unauthenticated probe.
    audit_lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert any('"ok": false' in line and '"stage": "auth"' in line for line in audit_lines)


def test_unknown_session_refused_without_queuing(tmp_path):
    """A caller targeting a non-existent session must NOT get a task
    created — otherwise a typo produces a permanently stuck task."""
    server, port, ingest, thread = _start_server(tmp_path, seed_sessions=("s1",))
    try:
        payload = {
            "jsonrpc": "2.0", "id": 5, "method": "tasks/send",
            "params": {
                "session_id": "nope",
                "message": {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
            },
        }
        conn = _http(port)
        conn.request("POST", "/", body=json.dumps(payload),
                     headers={"Content-Type": "application/json"})
        body = json.loads(conn.getresponse().read())
        assert body["error"]["code"] == -32002  # A2A_NOT_FOUND
        conn.close()
    finally:
        _stop(server, thread)

    # Nothing persisted for the bad session.
    assert ingest.store.list_requests_by_session("nope") == []
    assert ingest.store.list_mailbox_items("nope") == []


def test_internal_error_does_not_leak_details(tmp_path):
    """Unexpected exceptions in the handler must return a generic wire
    message; full context stays in the logger."""
    server, port, ingest, thread = _start_server(tmp_path)
    try:
        class _Boom:
            runtime_root = ingest.store.runtime_root
            def __getattr__(self, name):
                raise RuntimeError(f"/etc/secret/path internal detail {name}")
        server.ingest = _Boom()  # type: ignore[assignment]
        payload = {
            "jsonrpc": "2.0", "id": 7, "method": "tasks/send",
            "params": {
                "session_id": "s1",
                "message": {"role": "user", "parts": [{"type": "text", "text": "x"}]},
            },
        }
        conn = _http(port)
        conn.request("POST", "/", body=json.dumps(payload),
                     headers={"Content-Type": "application/json"})
        body = json.loads(conn.getresponse().read())
        assert body["error"]["code"] == -32603
        assert body["error"]["message"] == "Internal server error"
        assert "/etc/secret/path" not in body["error"]["message"]
        conn.close()
    finally:
        _stop(server, thread)


def test_auth_enforced_when_token_configured(tmp_path):
    cfg = InboundGuardConfig(auth_token="s3cret", audit_path=tmp_path / "audit.jsonl")
    server, port, _, thread = _start_server(tmp_path, guard_config=cfg)
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tasks/send",
            "params": {
                "session_id": "s1",
                "message": {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
            },
        }
        # No Authorization header → rejected.
        conn = _http(port)
        conn.request("POST", "/", body=json.dumps(payload),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read())
        assert "error" in body
        assert body["error"]["code"] == -32003  # A2A_GUARD_REJECT
        conn.close()

        # With correct token → accepted.
        conn = _http(port)
        conn.request("POST", "/", body=json.dumps(payload),
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer s3cret"})
        resp = conn.getresponse()
        body = json.loads(resp.read())
        assert "result" in body
        conn.close()
    finally:
        _stop(server, thread)
