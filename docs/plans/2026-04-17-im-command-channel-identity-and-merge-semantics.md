# IM Command Channel Identity And Merge Semantics

**Date:** 2026-04-17  
**Status:** Frozen  
**Scope:** Telegram + Lark/Feishu command channels

## Goal

Freeze the product contract for IM command channels so implementation stops drifting.

This document is intentionally about **definition**, not code structure.
It answers:

1. What is the identity of an IM command channel?
2. What counts as "multiple chats"?
3. When should configs be merged?
4. What is singleton-scoped, and what is not?
5. What behavior must be identical across Telegram and Lark/Feishu?

If these semantics are not fixed first, implementation will continue to oscillate between:

- per-run
- per-process
- per-worktree
- per-daemon
- per-bot/app

and the same lifecycle/auth/observability bugs will keep recurring.

---

## Design Principle

`thin-supervisor` should treat IM command channels as **operator-facing command surfaces**, not as ad hoc transport instances.

The system must optimize for:

- clear product semantics
- deterministic ownership
- minimal user ambiguity
- identical behavior across Telegram and Lark/Feishu

The system must **not** optimize for:

- preserving arbitrary config duplication
- allowing multiple competing inbound owners
- transport-specific behavior differences unless explicitly documented

---

## Core Terms

### 1. Provider Instance

The underlying IM application identity.

- **Telegram:** one `bot_token` (key)
- **Lark/Feishu:** one `app_id` (key). `app_secret` is **not** part of the
  identity key — it is a transport-critical credential under the same
  identity and belongs in the must-match-exactly set (see Merge Rules).
  Two entries with the same `app_id` and different `app_secret` values
  must fail closed at startup.

This is the lowest-level identity for remote operator interaction.

### 2. Conversation Target

A concrete conversation entrypoint visible to humans.

- **Telegram:** one `chat_id`
- **Lark/Feishu:** one `open_chat_id`

This is what people mean when they say "one chat" or "multiple chats".

### 3. Authorized Principal

A concrete human identity allowed to issue commands.

- **Telegram:** `user_id`
- **Lark/Feishu:** `open_id` or equivalent user identity

### 4. Inbound Transport

The mechanism that receives operator commands.

- **Telegram:** polling or webhook transport
- **Lark/Feishu:** callback/event subscription transport

This is the part that must have a single owner.

### 5. Logical Command Channel

The product-level command surface exposed to operators.

A logical command channel consists of:

- one Provider Instance
- one active inbound transport owner
- one merged authorization surface
- one merged set of conversation targets
- one shared canonical operator API surface

---

## Primary Contract

### Rule 1: One Provider Instance = One Logical Command Channel

This is the most important rule in the system.

- One Telegram bot = one logical command channel
- One Lark/Feishu app = one logical command channel

This means the system must **not** treat multiple config entries that share the same provider identity as independent command channels.

### Rule 2: One Logical Command Channel May Serve Multiple Conversation Targets

Examples:

- one Telegram bot serving:
  - a personal chat
  - a team chat
  - an on-call chat
- one Feishu app serving:
  - one project group
  - one ops group
  - one private admin conversation

These are **multiple entrypoints into the same command channel**, not multiple command channels.

### Rule 3: One Provider Instance Has Exactly One Active Inbound Owner

At any point in time, only one process may own the inbound transport for a given Provider Instance.

Examples:

- only one process may poll a given Telegram bot
- only one process may own the callback server / event intake for a given Feishu app

This rule exists to prevent:

- duplicate update consumption
- offset races
- callback port contention
- non-deterministic command ownership

### Rule 4: Non-owner Processes May Still Send Outbound Notifications

Inbound ownership and outbound delivery are different concerns.

Therefore:

- only one process owns inbound command receiving
- any process may still send outbound alert messages through the same Provider Instance

This is required for:

- multi-worktree visibility
- daemon + foreground coexistence
- alert fanout without inbound duplication

---

## Merge Semantics

When the config contains multiple command-channel entries for the same Provider Instance, they must be merged into one logical command channel.

This merge happens **before** runtime ownership is decided.

### Example: Telegram

```yaml
notification_channels:
  - kind: telegram
    mode: command
    bot_token: "same-bot"
    chat_id: "chat_A"
    allowed_chat_ids: ["chat_A"]
    allowed_user_ids: ["alice"]

  - kind: telegram
    mode: command
    bot_token: "same-bot"
    chat_id: "chat_B"
    allowed_chat_ids: ["chat_B"]
    allowed_user_ids: ["bob"]
```

Equivalent single-entry form using the list field `chat_ids`:

```yaml
notification_channels:
  - kind: telegram
    mode: command
    bot_token: "same-bot"
    chat_ids: ["chat_A", "chat_B"]
    allowed_chat_ids: ["chat_A", "chat_B"]
    allowed_user_ids: ["alice", "bob"]
```

Either shape must become:

- one logical Telegram command channel
- conversation targets = `{chat_A, chat_B}`
- allowed chats = `{chat_A, chat_B}`
- allowed users = `{alice, bob}`

It must **not** become:

- two separate Telegram command channels fighting over the same bot

### Example: Lark / Feishu

```yaml
notification_channels:
  - kind: lark
    mode: command
    app_id: "same-app"
    allowed_chat_ids: ["oc_proj"]
    allowed_user_ids: ["ou_admin_1"]

  - kind: lark
    mode: command
    app_id: "same-app"
    allowed_chat_ids: ["oc_ops"]
    allowed_user_ids: ["ou_admin_2"]
```

This must become:

- one logical Feishu command channel
- conversation targets = `{oc_proj, oc_ops}`
- allowed chats = `{oc_proj, oc_ops}`
- allowed users = `{ou_admin_1, ou_admin_2}`

---

## Merge Rules

### Fields That Must Union

These fields must merge by set union:

- notification targets (see below — the legacy scalar `chat_id` and any
  `chat_ids` list both feed this set)
- `allowed_chat_ids`
- `allowed_user_ids`

**Legacy scalar `chat_id` mapping.** The existing Telegram config shape
has both a scalar `chat_id` (notification target) and a list
`allowed_chat_ids` (auth). Under the new model these collapse into one
unified "conversation targets" set per Provider Instance. Each config
entry contributes its `chat_id` (if present) plus every value in its
`chat_ids` list (if present) into that set. `allowed_chat_ids` then
unions across entries to form the auth allowlist. When a single entry
specifies only `allowed_chat_ids` (no `chat_id`), those same values
also become notification targets — there is exactly one set of
conversation targets per Provider Instance.

**Per-entry allowlist promotion.** An entry's *own* authorization
intent is preserved across the merge:

- If an entry specifies `chat_id`/`chat_ids` **without** its own
  `allowed_chat_ids`, those chats are promoted into the merged auth
  allowlist. This preserves the pre-merge single-entry default where
  `chat_id` alone means "this chat can both receive and issue
  commands".
- If an entry specifies `allowed_chat_ids` explicitly, that narrowing
  is honored: its `chat_id`/`chat_ids` are **not** auto-promoted. The
  entry becomes "send alerts to X, only Y may command", even after
  merging with other entries.

Without this rule, a legacy target-only entry merged with an
explicit-allowlist entry would silently lose command authorization
(still receive alerts, but `/pause`, `/ask`, etc. would be rejected),
which contradicts "merged chats have one authorization surface and can
issue commands if authorized".

### Fields That Must Match Exactly

These fields define transport behavior and must not silently diverge.

If they differ within the same Provider Instance, startup must fail closed with a configuration error.

Examples:

- `language`
- Feishu `app_secret` (credentials under the same `app_id` must agree)
- Feishu `callback_port`
- Feishu `verification_token`
- Feishu `encrypt_key`
- any future field that changes inbound transport behavior

### Fields That May Require Product Decision Later

These must not be guessed at implementation time.

Examples:

- default output verbosity
- default message formatting mode
- future per-channel display preferences

If such fields are introduced later, they must explicitly declare one of:

- `union`
- `exact-match`
- `unsupported-mixed-config`

---

## Multiple Chat Semantics

This section is the direct answer to the current ambiguity.

### Question

If there are two Telegram chats, what do we want?

### Answer

If two chats share the same Provider Instance, we want:

1. both chats to receive notifications
2. both chats to be able to issue commands, if authorized
3. both chats to operate on the same canonical run universe
4. only one inbound transport owner for the Provider Instance
5. one merged authorization surface, not separate hidden auth islands

### Important Consequence

This means:

- "multi-chat" is a **product surface** concern
- not a transport ownership concern

Do not model "one bot serving two chats" as "two command channels".

Model it as:

- one logical command channel
- multiple conversation targets

---

## Single Chat + Multiple Allowed Users

This is a simpler case and is explicitly valid.

Example:

- one Telegram chat
- multiple allowed user ids

This does **not** require channel merging across config entries.
It is just one command surface with a broader principal allowlist.

So the real design problem is not "multiple users".
The real design problem is:

> what happens when one Provider Instance appears in multiple config entries?

That answer is now:

> they merge into one logical command channel.

---

## Ownership Semantics

### Inbound Ownership

Exactly one process owns inbound transport per Provider Instance.

Ownership is about:

- who polls Telegram updates
- who receives Feishu callbacks
- who converts inbound remote messages into canonical operator actions

### Outbound Delivery

All processes may emit notifications through a Provider Instance.

Outbound delivery is about:

- pause alerts
- blocked alerts
- completion alerts
- human intervention alerts

**Fanout.** A single `notify(event)` call on a logical command channel
delivers the event to **every** merged conversation target for that
Provider Instance — not just the first one, and not just the one that
happened to be configured alongside the most recent entry. If future
routing policy (per-event target selection, severity-based filtering,
quiet hours, etc.) is introduced, it must be an explicit layer on top
of this default, not a silent divergence.

### Why These Are Separate

Inbound must be singleton for correctness.
Outbound must remain available from all processes for visibility.

This split is required and should remain explicit in docs and code.

---

## Canonical API Requirement

All IM command channels must be adapters over the same canonical operator API family.

Required action surface:

- `list_runs`
- `get_run_snapshot`
- `get_run_timeline`
- `get_recent_exchange`
- `explain_run`
- `explain_exchange`
- `assess_drift`
- `request_clarification`
- `pause_run`
- `resume_run`
- `add_operator_note`
- `list_operator_notes`

IM channels must **not**:

- parse tmux directly
- read transcript sources directly
- bypass `RunContext`
- bypass `OperatorActions`
- invent transport-specific control semantics

If Telegram and Feishu differ, the difference must be:

- presentation-only
- or explicitly documented as product policy

Never accidental.

---

## Clarification Contract

Phase 1 clarification is:

- operator asks the supervisor
- supervisor answers from explainer/mediator context
- the interaction is recorded in sideband events

Phase 1 clarification is **not**:

- a direct worker chat
- a transport-specific side conversation

Phase 2, if ever added, must still preserve this rule:

- IM channel asks supervisor
- supervisor decides whether to answer directly or ask the worker
- worker is never addressed directly by Telegram or Feishu

---

## Explicit Non-Goals

This document does **not** define:

- role-based permission systems beyond allowlists
- per-repo authorization policies
- multi-tenant bot routing
- human approval policies for destructive commands
- message formatting details
- whether Telegram or Feishu is the primary remote surface

Those can be designed later.

What this document freezes is only:

- identity
- merge semantics
- ownership semantics
- cross-process behavior
- canonical command surface assumptions

---

## Acceptance Criteria For Future Implementations

An implementation conforms to this spec only if:

1. One Provider Instance is represented as one logical command channel.
2. Multiple config entries for the same Provider Instance are merged.
3. Multiple chats under the same Provider Instance can all receive notifications.
4. Multiple chats under the same Provider Instance can all issue commands if authorized.
5. Only one process owns inbound transport for a Provider Instance.
6. Non-owner processes can still send outbound alerts.
7. Telegram and Feishu obey the same identity/merge/ownership model.
8. All command execution routes through the canonical operator action layer.
9. Mixed transport-critical fields fail closed instead of being silently guessed.

---

## Implementation Checklist

Any engineer implementing or refactoring IM command channels should answer these questions before writing code:

1. What is the Provider Instance key?
2. What data merges by union?
3. What data must match exactly?
4. Who owns inbound transport?
5. How do non-owner processes still emit outbound alerts?
6. Are multiple chats first-class command entrypoints?
7. Does this adapter call only canonical operator APIs?

If any answer is unclear, the implementation is not ready.

---

## One-Sentence Summary

**One Provider Instance equals one logical IM command channel: one inbound owner, multiple conversation targets, merged allowlists, shared canonical operator semantics, and outbound notifications available from all processes.**
