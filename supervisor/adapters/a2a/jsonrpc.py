"""JSON-RPC 2.0 framing + agent-card builder for the A2A adapter.

Only the narrow subset A2A actually uses: single request → single
response, ``id`` required, no batch, no notifications. Anything else
raises ``JSONRPCParseError`` which the HTTP layer turns into a 400.
"""
from __future__ import annotations

import json
from typing import Any

# JSON-RPC 2.0 standard error codes used by A2A.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Reserved application range starts at -32000.
A2A_FORBIDDEN = -32001
A2A_NOT_FOUND = -32002
A2A_GUARD_REJECT = -32003


class JSONRPCParseError(Exception):
    pass


def parse_request(body: bytes) -> tuple[str, dict, Any]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JSONRPCParseError(f"parse error: {exc}") from exc
    if not isinstance(payload, dict):
        raise JSONRPCParseError("jsonrpc request must be an object")
    if payload.get("jsonrpc") != "2.0":
        raise JSONRPCParseError("jsonrpc version must be 2.0")
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise JSONRPCParseError("method is required and must be a string")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise JSONRPCParseError("params must be an object")
    # A2A does not support JSON-RPC notifications — every request must
    # carry an id so the caller can correlate the response.  Distinguish
    # "missing" from "explicitly null": the latter would still land as a
    # notification-shaped request on the wire, so reject both.
    if "id" not in payload or payload.get("id") is None:
        raise JSONRPCParseError("id is required (notifications not supported)")
    rpc_id = payload["id"]
    return method, params, rpc_id


def build_response(*, rpc_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def build_error(*, rpc_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def build_agent_card(*, host: str, port: int, auth_required: bool) -> dict:
    return {
        "name": "thin-supervisor",
        "description": "Session-first long-running task supervisor",
        "url": f"http://{host}:{port}",
        "skills": [
            {
                "id": "submit_task",
                "description": (
                    "Submit an external task to a supervisor session. "
                    "Returns a task_id (= request_id) that persists across restarts."
                ),
            },
            {
                "id": "query_task",
                "description": "Query status + accumulated results of a previously submitted task_id.",
            },
        ],
        "authentication": {"required": auth_required, "type": "bearer"},
    }
