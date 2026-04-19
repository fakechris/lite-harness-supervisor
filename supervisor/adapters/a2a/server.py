"""Stdlib ``ThreadingHTTPServer`` hosting the A2A JSON-RPC endpoint.

Zero-dep by design — matches the project's existing philosophy. The
server accepts:

- ``GET /.well-known/agent.json`` → agent card
- ``POST /`` → JSON-RPC 2.0 for ``tasks/send`` + ``tasks/get``

Each request runs on a thread from ``ThreadingHTTPServer``'s pool. The
handler holds no shared state beyond the injected ``EventPlaneIngest``
and ``InboundGuard`` (both are thread-safe for the operations we make:
the store appends under fcntl lock; the guard's rate_limiter holds its
own lock).

Durability: task_id is the ``request_id`` persisted to
``external_tasks.jsonl``. Survives server and daemon restart.
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from supervisor.boundary.guard import InboundGuard
from supervisor.boundary.models import InboundGuardConfig, InboundRequest
from supervisor.event_plane.ingest import EventPlaneIngest

from .jsonrpc import (
    A2A_GUARD_REJECT,
    A2A_NOT_FOUND,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JSONRPCParseError,
    build_agent_card,
    build_error,
    build_response,
    parse_request,
)
from .task_mapper import (
    A2AGetError,
    A2ASendError,
    handle_tasks_get,
    handle_tasks_send,
)

logger = logging.getLogger(__name__)

_MAX_BODY = 1 << 20  # 1 MiB — generous; inbound payloads are small


class _A2AHandler(BaseHTTPRequestHandler):
    # set by A2AServer before any request is served
    server: "A2AServer"

    def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover — just silence default stderr
        logger.debug(fmt, *args)

    # ------------------------------------------------------------------
    # endpoints
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        if self.path == "/.well-known/agent.json":
            card = build_agent_card(
                host=self.server.host,
                port=self.server.port,
                auth_required=bool(self.server.guard_config.auth_token),
            )
            self._write_json(200, card)
            return
        self._write_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path not in ("/", ""):
            self._write_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._write_json(400, build_error(rpc_id=None, code=PARSE_ERROR, message="bad Content-Length"))
            return
        if length < 0 or length > _MAX_BODY:
            self._write_json(413, build_error(rpc_id=None, code=PARSE_ERROR, message="body too large"))
            return
        body = self.rfile.read(length) if length else b""

        try:
            method, params, rpc_id = parse_request(body)
        except JSONRPCParseError as exc:
            self._write_json(400, build_error(rpc_id=None, code=PARSE_ERROR, message=str(exc)))
            return

        client_id = self.client_address[0] if self.client_address else ""
        headers = {k: v for k, v in self.headers.items()}
        inbound = InboundRequest(
            client_id=client_id,
            text=self._extract_text_for_guard(method, params),
            transport="a2a",
            headers=headers,
        )

        try:
            # Run the boundary guard BEFORE method dispatch so every POST
            # — including unknown methods, malformed params, and empty-
            # text sends — is subject to auth, rate-limit, injection
            # scan, redaction, and audit.  The call is inside the try
            # block so that unexpected failures in audit / rate-limit /
            # redaction code paths surface as a scrubbed internal error
            # instead of propagating raw tracebacks through the HTTP
            # layer.
            guard_result = self.server.guard.check(inbound)
            if not guard_result.ok:
                self._write_json(
                    200,
                    build_error(
                        rpc_id=rpc_id,
                        code=A2A_GUARD_REJECT,
                        message=f"guard rejected: stage={guard_result.stage} reason={guard_result.reason}",
                    ),
                )
                return

            if method == "tasks/send":
                result = handle_tasks_send(
                    params=params,
                    ingest=self.server.ingest,
                    normalized_text=guard_result.normalized_text,
                    client_id=client_id,
                )
                self._write_json(200, build_response(rpc_id=rpc_id, result=result))
                return
            if method == "tasks/get":
                result = handle_tasks_get(params=params, store=self.server.ingest.store)
                self._write_json(200, build_response(rpc_id=rpc_id, result=result))
                return
            self._write_json(
                200, build_error(rpc_id=rpc_id, code=METHOD_NOT_FOUND, message=f"unknown method: {method}")
            )
        except A2ASendError as exc:
            self._write_json(200, build_error(rpc_id=rpc_id, code=exc.code, message=str(exc)))
        except A2AGetError as exc:
            self._write_json(200, build_error(rpc_id=rpc_id, code=exc.code, message=str(exc)))
        except ValueError as exc:
            self._write_json(200, build_error(rpc_id=rpc_id, code=INVALID_PARAMS, message=str(exc)))
        except Exception:  # noqa: BLE001 — boundary between untrusted peer and us
            # Do not leak ``str(exc)`` — unexpected exceptions can carry
            # filesystem paths, SQL fragments, or internal identifiers
            # that should stay server-side.  Full context is in the log.
            logger.exception("A2A handler internal error")
            self._write_json(
                200,
                build_error(rpc_id=rpc_id, code=INTERNAL_ERROR, message="Internal server error"),
            )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text_for_guard(method: str, params: dict) -> str:
        """Pull the text that we will scan / redact.

        ``tasks/send`` has the caller's prompt in ``params.message.parts``.
        ``tasks/get`` has no user-controlled text; an empty string is
        safe input to the guard (auth + rate_limit still run).
        """
        if method != "tasks/send":
            return ""
        message = params.get("message") or {}
        parts = message.get("parts") or []
        if not isinstance(parts, list):
            return ""
        chunks: list[str] = []
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            text = part.get("text", "")
            # Only real strings contribute. Guard against null / numeric
            # payloads that would otherwise be coerced to "None"/"1" and
            # silently feed the injection scanner garbage.
            if isinstance(text, str):
                chunks.append(text)
        return "".join(chunks)

    def _write_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class A2AServer(ThreadingHTTPServer):
    """Threaded HTTP server wrapping ``_A2AHandler``.

    Attributes (read by the handler) are stable after ``__init__`` and
    must not change while the server is running.
    """

    daemon_threads = True  # workers die with the process

    def __init__(
        self,
        *,
        host: str,
        port: int,
        ingest: EventPlaneIngest,
        guard_config: InboundGuardConfig,
    ):
        super().__init__((host, port), _A2AHandler)
        self.host = host
        # ``port`` may have been 0 (kernel-assigned); read the resolved port
        self.port = self.server_address[1]
        self.ingest = ingest
        self.guard_config = guard_config
        self.guard = InboundGuard(guard_config)
