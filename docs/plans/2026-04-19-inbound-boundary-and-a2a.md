# Inbound Boundary Guard + A2A Adapter — Execution Plan

**Date:** 2026-04-19
**Branch:** `feat/inbound-boundary-and-a2a`
**Scope:** two features, shipped as one branch with per-task commits, single PR at the end.

---

## Motivation

Today `EventPlaneIngest` trusts its callers — mailbox/request writes have no content scrubbing, no auth, no rate limit. The daemon is socket-local so that has been acceptable, but two adjacent needs push us to build a proper ingress boundary:

1. **Any future inbound ingest path** (webhooks, HTTP, A2A, CLI from unprivileged callers) needs a uniform safety layer. Inline-in-adapter checks don't compose.
2. **A2A peer protocol** is worth supporting so other agents / another supervisor instance can submit tasks. Reference design (`iamagenius00/hermes-a2a`) shows a decent safety template but a bad orchestration model (synchronous blocking, in-memory LRU). We lift their safety layer, discard their sync model — our `event_plane` already has durable async request/wait/mailbox, so A2A is a thin HTTP shell on top.

These two features are sequenced deliberately. Phase 1 is independently valuable and lives at a lower layer. Phase 2 is A2A's first real consumer.

---

## Grounding (verified 2026-04-19)

- Zero runtime deps (`pyproject.toml` lists only PyYAML). **A2A must use stdlib `http.server`** — no new deps.
- `supervisor/adapters/` already holds platform adapters (`telegram_channel.py`, `lark_channel.py`, etc.); A2A belongs here.
- `supervisor/boundary/` is a new namespace. No existing module by that name.
- `supervisor/event_plane/ingest.py` `EventPlaneIngest.register_request(...)` does request + wait + mailbox creation in one call — A2A `tasks/send` maps onto it directly. No new ingest code needed.
- `supervisor/storage/state_store._atomic_append_line` is the existing fcntl-locked append helper; audit log reuses it.
- `.supervisor/runtime/shared/` is the canonical cross-session home (`supervisor/learning._shared_dir()`).

---

## Design constraints (load-bearing)

1. **Async by construction.** `tasks/send` returns `task_id` + `state=queued` immediately. We **do not** block HTTP on agent reply. This is the explicit departure from hermes-a2a's design.
2. **`task_id == request_id`.** Not an in-memory LRU key — a durable, first-class identifier that survives daemon / adapter restarts.
3. **Guard is transport-agnostic.** Takes abstract `InboundRequest(client_id, text, headers, …)`. HTTP is one wrapper; future CLI / webhook callers use the same chain.
4. **Guard components are independently toggleable.** Config dataclass; each component has a default and can be disabled for internal callers (e.g., daemon self-writes skip rate-limit but still run redaction).
5. **Guard never blocks the core daemon process.** A2A adapter is a **separate process** (`thin-supervisor a2a serve`). Daemon and A2A share the event-plane JSONLs via `_atomic_append_line`; no shared memory.
6. **Audit log goes to `.supervisor/runtime/shared/inbound_audit.jsonl`** via `_atomic_append_line`. Same pattern as `system_events.jsonl`.

---

## Phase 1: Inbound Boundary Guard

New module tree:

```
supervisor/boundary/
  __init__.py
  models.py          # InboundRequest, GuardResult, InboundGuardConfig
  auth.py            # bearer-token + localhost-only fallback
  rate_limit.py      # sliding window per client_id, thread-safe
  injection.py       # pattern-based inbound text scan
  redaction.py       # outbound scrub (API keys, tokens, emails)
  audit.py           # append JSONL via _atomic_append_line
  guard.py           # InboundGuard facade: chain of above
```

### Task 1.1 — `models.py` + `InboundGuardConfig`

**Test file:** `tests/test_boundary_models.py`

- `InboundRequest(client_id: str, text: str, headers: dict, transport: str)` frozen dataclass.
- `GuardResult(ok: bool, reason: str, stage: str, normalized_text: str)` frozen dataclass.
- `InboundGuardConfig(enable_auth: bool = True, enable_rate_limit: bool = True, enable_injection_scan: bool = True, enable_redaction: bool = True, enable_audit: bool = True, auth_token: str = "", rate_limit_per_minute: int = 20, audit_path: Path | None = None)`.

### Task 1.2 — Auth

**Test file:** `tests/test_boundary_auth.py`

- `check_auth(req, config) -> GuardResult`
- Rules:
  - `config.auth_token` unset → accept iff `client_id` resolves to localhost (`127.0.0.1` / `::1` / UNIX socket peer)
  - `config.auth_token` set → require `Authorization: Bearer <token>` header match
  - Mismatch / missing → `GuardResult(ok=False, stage="auth", reason="invalid or missing token")`

### Task 1.3 — Rate limiter

**Test file:** `tests/test_boundary_rate_limit.py`

- `RateLimiter(per_minute: int)` — sliding window per `client_id`
- `check(client_id) -> bool` under `threading.Lock`
- Test: concurrent 1000 calls from 10 clients, each client bounded to `per_minute`.

### Task 1.4 — Injection scanner

**Test file:** `tests/test_boundary_injection.py`

Patterns (port from `hermes-a2a/security/a2a_security.py`, verify and trim):
1. `ignore previous instructions`
2. `disregard the above`
3. `you are now` (role reassignment)
4. `</system>` / `<|im_start|>system` (template escape)
5. `execute the following code`
6. `<script` / `javascript:` (XSS via reflection)
7. system-prompt leakage probes (`show your system prompt`, `repeat the above`)

`scan(text) -> GuardResult`. Case-insensitive. Returns first match's stage.

### Task 1.5 — Outbound redactor

**Test file:** `tests/test_boundary_redaction.py`

- API keys: `sk-[A-Za-z0-9]{20,}`, `xoxb-[A-Za-z0-9-]{10,}`, `ghp_[A-Za-z0-9]{30,}`
- AWS: `AKIA[0-9A-Z]{16}`
- Bearer tokens in-text
- Emails (only if `redact_emails=True`; default False — emails are often legitimate context)
- JWT: `ey[A-Za-z0-9_-]{10,}\.ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+`
- Replace with `[REDACTED:<kind>]`. Return new string.

### Task 1.6 — Audit log

**Test file:** `tests/test_boundary_audit.py`

- `append_audit(path, record)` via `_atomic_append_line`
- Record shape: `{ts, transport, client_id, stage, ok, reason, text_hash}` (hash not raw text — audit without PII)
- Concurrent-write test: 50 threads append, final JSONL line count = 50.

### Task 1.7 — `InboundGuard` facade

**Test file:** `tests/test_boundary_guard.py`

- `InboundGuard(config).check(req) -> GuardResult`
- Chain order: auth → rate_limit → injection_scan → (text normalized via redaction for downstream payload storage) → audit
- Short-circuits on first failure; always writes audit entry (pass or fail).
- Disabled components are skipped cleanly.

**Commit:** `feat(boundary): add inbound guard with auth/rate-limit/injection/redaction/audit`

---

## Phase 2: A2A Inbound Adapter

New module tree:

```
supervisor/adapters/a2a/
  __init__.py
  server.py          # stdlib http.server, agent card + JSON-RPC
  jsonrpc.py         # parse / build JSON-RPC 2.0 frames
  task_mapper.py     # A2A ↔ EventPlaneIngest translation
```

CLI: new subcommand `thin-supervisor a2a serve [--port 8081] [--host 127.0.0.1] [--token-env SUPERVISOR_A2A_TOKEN]`.

### Task 2.1 — JSON-RPC helpers + agent card

**Test file:** `tests/test_a2a_jsonrpc.py`

- `parse_request(body: bytes) -> (method, params, id) | ParseError`
- `build_response(id, result) -> dict`; `build_error(id, code, message) -> dict`
- Agent card endpoint: `GET /.well-known/agent.json` returns:
  ```json
  {
    "name": "thin-supervisor",
    "url": "http://<host>:<port>",
    "skills": [
      {"id": "submit_task", "description": "submit an external task to a supervisor session"},
      {"id": "query_task", "description": "query status + results of a submitted task"}
    ],
    "authentication": {"required": <bool>, "type": "bearer"}
  }
  ```

### Task 2.2 — `tasks/send` → `register_request`

**Test file:** `tests/test_a2a_tasks_send.py`

Flow:
1. `POST /` JSON-RPC with `method=tasks/send`, `params={id, message: {parts: [{type:text, text:...}]}, session_id, task_kind?, deadline_at?}`
2. `InboundGuard.check(InboundRequest(client_id=peer_addr, text=message_text, headers, transport="a2a"))`
3. On guard pass: resolve `session_id` against `collect_sessions()` — reject with JSON-RPC error if unknown.
4. `ingest.register_request(session_id=..., provider="a2a", target_ref=<caller task id>, task_kind=<or "external_review">, deadline_at=...)` — returns our `request_id`.
5. Immediately append a `SessionMailboxItem` with the caller's text as summary + payload so the session sees content on wake. (Alternatively: extend `register_request` to accept initial payload — cleaner but more invasive; decide at impl time.)
6. Respond with `{id: rpc_id, result: {id: request_id, status: {state: "queued"}}}`.

**Invariants tested:**
- `request_id` returned == what persists in `external_tasks.jsonl`.
- Guard failure → JSON-RPC error, **no** store writes (audit still logged).
- Unknown `session_id` → error, no writes.
- Restart-survival: request written, adapter restarted, `latest_request(request_id)` still returns it.

### Task 2.3 — `tasks/get` → read path

**Test file:** `tests/test_a2a_tasks_get.py`

1. `POST /` JSON-RPC with `method=tasks/get`, `params={id: <request_id>}`
2. Guard (rate-limit only; reads are cheap).
3. `store.latest_request(request_id)` + `store.list_results_for_request(request_id)`
4. Map to A2A task status:
   - `status.state`: `queued` / `in_progress` / `completed` / `failed` / `cancelled` (derive from `request.status`)
   - `artifacts`: one per result, `{type: "text", text: result.summary, metadata: result.payload}`
5. Unknown `request_id` → JSON-RPC error.

### Task 2.4 — HTTP server + CLI subcommand

**Test file:** `tests/test_a2a_server.py` (uses `http.client` against `ThreadingHTTPServer` bound to `127.0.0.1:0`)

- `A2AServer(runtime_root, config)` class wrapping `ThreadingHTTPServer`.
- Handler dispatches: `GET /.well-known/agent.json`, `POST /` → JSON-RPC router.
- Clean shutdown on SIGTERM / SIGINT.
- `supervisor/app.py`: register `a2a serve` subparser.
- `--token-env` reads token from env; if absent, localhost-only mode.

### Task 2.5 — Overview integration + docs

**Test file:** `tests/test_system_overview.py` (extend existing)

- If A2A server is running (detect via PID file at `.supervisor/runtime/shared/a2a.pid` or a `daemon_started`-style entry), `overview` shows a line `A2A: listening on <host>:<port>`.
- README section under Features: "A2A inbound adapter"
- New `docs/a2a.md`: quick start, security notes, mapping table (A2A method → event_plane call).
- CHANGELOG entry under `Unreleased`.

**Commit cadence:** commit after each of 2.1 – 2.5. Final test sweep + open PR.

---

## Out of scope (v1)

- A2A **outbound** (supervisor calling remote A2A agents). Adds complexity; deferred until first concrete need — likely when we refactor `external_review` providers.
- A2A streaming (SSE). Our `tasks/get` polling model is enough.
- `tasks/cancel`. Map to `append_wait` with `status="cancelled"` later when we have the UX for it.
- Agent skill discovery from supervisor's actual capabilities. v1 hardcodes two skills; v2 can reflect from session metadata.

---

## Verification

Per-task targeted suites run at each commit. Final sweep:

```bash
pytest -q tests/test_boundary_models.py tests/test_boundary_auth.py \
  tests/test_boundary_rate_limit.py tests/test_boundary_injection.py \
  tests/test_boundary_redaction.py tests/test_boundary_audit.py \
  tests/test_boundary_guard.py \
  tests/test_a2a_jsonrpc.py tests/test_a2a_tasks_send.py \
  tests/test_a2a_tasks_get.py tests/test_a2a_server.py \
  tests/test_event_plane_store.py tests/test_system_overview.py \
  tests/test_app_cli.py
pytest -q    # full suite, must stay at 1076+ green
```

Manual smoke:

```bash
# Phase 1 — guard is library-level, no CLI smoke
# Phase 2 — end-to-end
SUPERVISOR_A2A_TOKEN=dev thin-supervisor a2a serve --port 8081 &
curl -s http://127.0.0.1:8081/.well-known/agent.json | jq .
curl -s -X POST http://127.0.0.1:8081 \
  -H "Authorization: Bearer dev" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tasks/send","params":{"session_id":"<s>","message":{"role":"user","parts":[{"type":"text","text":"please review PR 123"}]}}}' | jq .
# expect: {result: {id: "req_...", status: {state: "queued"}}}
thin-supervisor status                 # target session should show mailbox_new +1
thin-supervisor overview               # should list A2A listener
# now simulate completion (from the session's worker path) and re-query:
curl -s -X POST http://127.0.0.1:8081 \
  -H "Authorization: Bearer dev" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"2","method":"tasks/get","params":{"id":"<request_id>"}}' | jq .
```

---

## Acceptance criteria

1. `InboundGuard` is standalone — importable from a future webhook handler with no A2A dep.
2. A2A `tasks/send` never blocks on agent reply — response time bounded by disk append latency.
3. `task_id` (= `request_id`) persists across adapter and daemon restart; `tasks/get` still resolves it.
4. Every inbound A2A request (pass or fail) produces exactly one line in `inbound_audit.jsonl`.
5. Guard failure produces a JSON-RPC error response AND a store-write of zero records on the event-plane side.
6. `overview` shows the A2A listener when running.
7. Full suite green (1076+).

---

## Execution model

One branch `feat/inbound-boundary-and-a2a`. Commit after every task (1.1 … 2.5). Full suite after each commit. Single PR at the end. If any red→green surfaces scope creep, stop and revise this file before writing more code.
