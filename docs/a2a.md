# A2A Inbound Adapter

Thin-supervisor speaks [Google's A2A](https://github.com/google/A2A)
JSON-RPC 2.0 protocol for **inbound** task submission — other agents
(another supervisor instance, a Hermes agent, any A2A-compatible client)
can send tasks into a session's mailbox and poll for results.

This adapter is **inbound only** in v1. Supervisor itself is not an A2A
client; outbound A2A calls may come later.

## Why this shape

The reference A2A implementation we studied
([`iamagenius00/hermes-a2a`](https://github.com/iamagenius00/hermes-a2a))
injects inbound messages **synchronously** into a single live chat
session and blocks the HTTP request until the agent replies. That works
for individual-chat agents but is incompatible with durable long-running
tasks.

Our adapter is async by construction:

- `tasks/send` returns immediately with `state=queued` and a durable
  `task_id`.
- `task_id == request_id` — first-class event-plane object, persisted
  to `external_tasks.jsonl`, survives adapter and daemon restarts.
- `tasks/get` polls the event-plane store for updates — results land
  when a supervisor session processes the mailbox item and calls
  `ingest_result`.

## Quick start

```bash
# Optional: set a bearer token; otherwise the server is localhost-only.
export SUPERVISOR_A2A_TOKEN=your-secret

thin-supervisor a2a serve --port 8081
```

The listener advertises itself through `.supervisor/runtime/shared/
system_events.jsonl`, so `thin-supervisor overview` shows:

```
A2A adapter listening on 127.0.0.1:8081 (auth-required)
```

## Agent card

```bash
curl -s http://127.0.0.1:8081/.well-known/agent.json | jq .
```

Returns `{name, url, skills: [submit_task, query_task], authentication}`.

## tasks/send

```bash
curl -s -X POST http://127.0.0.1:8081 \
  -H "Authorization: Bearer $SUPERVISOR_A2A_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tasks/send",
    "params": {
      "session_id": "<supervisor-session-id>",
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "please review PR 42"}]
      },
      "task_kind": "external_review",
      "deadline_at": "2026-04-19T23:59:59+00:00"
    }
  }'
```

Response:

```json
{"jsonrpc": "2.0", "id": "1",
 "result": {"id": "req_abc123...", "status": {"state": "queued"}}}
```

The adapter:

1. Runs the inbound payload through `InboundGuard` (auth, rate-limit,
   injection scan, redaction, audit).
2. Creates an `ExternalTaskRequest(provider="a2a", ...)` + `SessionWait`
   via `EventPlaneIngest.register_request`.
3. Seeds a `SessionMailboxItem(source_kind="a2a_inbound", ...)` with
   the redacted text.
4. Returns the persisted `request_id` as the A2A `task.id`.

## tasks/get

```bash
curl -s -X POST http://127.0.0.1:8081 \
  -H "Authorization: Bearer $SUPERVISOR_A2A_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"2","method":"tasks/get","params":{"id":"req_abc123..."}}'
```

Status mapping (supervisor → A2A):

| Supervisor `ExternalTaskRequest.status` | A2A `state`   |
| --------------------------------------- | ------------- |
| `pending`                               | `queued`      |
| `in_flight`                             | `in_progress` |
| `completed`                             | `completed`   |
| `failed`                                | `failed`      |
| `expired`                               | `cancelled`   |

Accumulated results come back as `artifacts: [{type, text, metadata}]`.

## Security

Every inbound request passes through
[`supervisor.boundary.InboundGuard`](../supervisor/boundary/guard.py).
Components, each independently toggleable:

| Layer        | Default behaviour                                                    |
| ------------ | -------------------------------------------------------------------- |
| Auth         | Bearer token when `SUPERVISOR_A2A_TOKEN` set; else localhost-only    |
| Rate limit   | 20 req/min per client IP (sliding window, thread-safe)               |
| Injection    | 8 pattern scans (role reassignment, template escape, script, etc.)   |
| Redaction    | JWT, GitHub / Slack / AWS / OpenAI-style keys replaced in payload    |
| Audit        | Append-only JSONL at `.supervisor/runtime/shared/inbound_audit.jsonl`; SHA-256 text hash only |

Audit records never include raw text — only a SHA-256 hash — so the
audit log itself is safe to retain.

## Method → event-plane mapping

| A2A method     | Event-plane call                                                                                        |
| -------------- | ------------------------------------------------------------------------------------------------------- |
| `tasks/send`   | `EventPlaneIngest.register_request` + `EventPlaneStore.append_mailbox_item`                             |
| `tasks/get`    | `EventPlaneStore.latest_request` + `EventPlaneStore.list_results_for_request`                           |

Not implemented (deferred past v1):

- `tasks/cancel` — will map to `append_wait(status="cancelled")` when UX is clear.
- SSE streaming — polling `tasks/get` is enough for most consumers.
- Outbound A2A — requires refactoring external_review providers; no concrete need yet.

## Limitations (v1)

- Agent card skills are hardcoded (`submit_task`, `query_task`). A v2
  could reflect from actual session capabilities.
- Session discovery is caller-provided: you must already know the
  target `session_id`. Use `thin-supervisor overview --json` to enumerate.
- Rate limit is per client IP. Behind a shared proxy this degrades
  into per-proxy — replace with a header-based identity if needed.
