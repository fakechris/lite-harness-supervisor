"""Tests for A2A JSON-RPC 2.0 framing + agent-card builder.

We target only the subset of JSON-RPC 2.0 the A2A protocol actually
uses for tasks/send + tasks/get: a request object with ``method``,
``params``, ``id``; a response with ``result`` or ``error``. Batched
calls and notifications (id absent) are explicitly out of scope.
"""
from __future__ import annotations

import json

from supervisor.adapters.a2a.jsonrpc import (
    JSONRPCParseError,
    build_agent_card,
    build_error,
    build_response,
    parse_request,
)


def test_parse_request_extracts_method_params_id():
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tasks/send",
        "params": {"session_id": "s1", "message": {"role": "user", "parts": []}},
    }).encode("utf-8")
    method, params, rpc_id = parse_request(body)
    assert method == "tasks/send"
    assert params["session_id"] == "s1"
    assert rpc_id == "1"


def test_parse_request_accepts_missing_params():
    body = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tasks/get"}).encode()
    method, params, rpc_id = parse_request(body)
    assert params == {}
    assert rpc_id == 2


def test_parse_request_rejects_non_json():
    try:
        parse_request(b"not json")
    except JSONRPCParseError as exc:
        assert "parse" in str(exc).lower()
    else:
        raise AssertionError("expected JSONRPCParseError")


def test_parse_request_rejects_wrong_jsonrpc_version():
    body = json.dumps({"jsonrpc": "1.0", "id": 1, "method": "x"}).encode()
    try:
        parse_request(body)
    except JSONRPCParseError as exc:
        assert "jsonrpc" in str(exc).lower()
    else:
        raise AssertionError("expected JSONRPCParseError")


def test_parse_request_rejects_missing_method():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "params": {}}).encode()
    try:
        parse_request(body)
    except JSONRPCParseError:
        pass
    else:
        raise AssertionError("expected JSONRPCParseError")


def test_build_response_shape():
    msg = build_response(rpc_id="1", result={"id": "req_x", "status": {"state": "queued"}})
    assert msg == {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {"id": "req_x", "status": {"state": "queued"}},
    }


def test_build_error_shape():
    msg = build_error(rpc_id=1, code=-32000, message="nope")
    assert msg == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "nope"},
    }


def test_build_agent_card_without_auth():
    card = build_agent_card(host="127.0.0.1", port=8081, auth_required=False)
    assert card["name"] == "thin-supervisor"
    assert card["url"] == "http://127.0.0.1:8081"
    assert card["authentication"]["required"] is False
    assert any(s["id"] == "submit_task" for s in card["skills"])
    assert any(s["id"] == "query_task" for s in card["skills"])


def test_build_agent_card_with_auth():
    card = build_agent_card(host="0.0.0.0", port=9000, auth_required=True)
    assert card["url"] == "http://0.0.0.0:9000"
    assert card["authentication"]["required"] is True
    assert card["authentication"]["type"] == "bearer"
