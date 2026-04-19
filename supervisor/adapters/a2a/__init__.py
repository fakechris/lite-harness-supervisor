"""A2A (Agent-to-Agent) inbound adapter for thin-supervisor.

Speaks Google A2A JSON-RPC 2.0 at ``.well-known/agent.json`` + ``POST /``.
Methods wired in v1:

- ``tasks/send`` — submit external task to a supervisor session
- ``tasks/get`` — poll task status + results

Every inbound request goes through ``supervisor.boundary.InboundGuard``
before touching the event-plane store. Task IDs returned to callers are
``request_id`` values from ``ExternalTaskRequest`` — durable, survive
restart.
"""
from .jsonrpc import (
    JSONRPCParseError,
    build_agent_card,
    build_error,
    build_response,
    parse_request,
)

__all__ = [
    "JSONRPCParseError",
    "build_agent_card",
    "build_error",
    "build_response",
    "parse_request",
]
