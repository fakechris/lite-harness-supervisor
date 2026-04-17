# Harness Rule Inventory (Slice 1 deliverable)

Companion to `docs/plans/2026-04-17-fat-skill-thin-harness-rule-repartitioning.md`.

One row per current semantic rule site in the supervisor harness. The
purpose is to make explicit which rules are **mechanism** (stay in the
harness) and which are **semantic** (move to the skill / structured
protocol / normalizer in later slices).

## Legend

- **Category** — `mechanism` (delivery/state-machine/budget) or `semantic`
  (meaning-of-checkpoint rules).
- **Mode** — current implementation mode:
  - `regex` — pattern list in `rules.py`
  - `prose-match` — keyword matching on reason / status strings
  - `heuristic` — compound inference from text / evidence
  - `hard-rule` — deterministic structural / status / threshold check
  - `mechanism` — timer, budget, counter, state-machine
  - `structured` — already consumes a typed / structured field
- **Target home** — where this rule family ends up after the repartitioning:
  - `harness` — stays in the runtime (mechanism rule)
  - `skill/protocol` — moves to worker-declared structured field
  - `normalizer` — consolidated into the single normalization layer
  - `harness fail-safe` — small hard-rule set kept as override
  - `optional judge` — rare fallback, not hot path
- **Slice** — migration slice that owns the move.

---

## A. `supervisor/gates/rules.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `SOFT_CONFIRMATION_PATTERNS` (L4) | Affirmative signals ("say go", "keep driving") that permit CONTINUE without escalation | semantic | regex | normalizer (advisory) + small `sem.*` fallback | 2–3 |
| 2 | `MISSING_EXTERNAL_INPUT_PATTERNS` (L14) | Credential / access / input-wanted signals | semantic | regex | skill/protocol (`blocking_inputs`, `escalation_class=business`) | 2–3 |
| 3 | `DANGEROUS_ACTION_PATTERNS` (L21) | "delete production" / "drop table" / "force push" / "永久删除" | semantic | regex | skill/protocol (`requires_authorization`) + narrow harness fail-safe on deterministic primitives | 2–3 |
| 4 | `BLOCKED_PATTERNS` (L29) | "blocked" / "cannot proceed" / "无法继续" | semantic | regex | skill/protocol (`escalation_class` + `blocking_inputs`) | 2–3 |
| 5 | `classify_text()` (L36) | Lexical classifier that returns one of 4 class names | semantic | heuristic | normalizer (compat fallback only) | 3 |
| 6 | `EXECUTION_EVIDENCE_PATTERNS` (L63) | 21 patterns for real-work evidence (tests, diffs, verifier signals) | semantic | regex | skill/protocol (`progress_class=execution` + `evidence_scope=current_node`) | 2–3 |
| 7 | `is_admin_only_evidence()` (L101) | Attach-boundary heuristic over `evidence` | semantic | heuristic | normalizer; heuristic stays as compat fallback | 3 |
| 8 | `classify_checkpoint()` (L138) | Flatten + classify full checkpoint | semantic | heuristic | normalizer consumes structured fields; this stays as fallback | 3 |

## B. `supervisor/gates/escalation.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 9 | `ESCALATION_CLASSES` tuple (L19) | Set of 3 escalation class names | semantic | constant | superseded by `escalation_class` + `reason_code=esc.*` | 2–3 |
| 10 | `_ESCALATION_REASON` mapping (L27) | class → (prose, confidence) | semantic | prose-match | replace with `reason_code` + confidence mapping | 1 (emit), 3 (consume) |
| 11 | `classify_for_escalation()` (L34) | Unified class picker reused by loop and continue gate | semantic | heuristic | normalizer (structured first, then compat) | 3 |
| 12 | `escalation_decision()` (L51) | SupervisorDecision builder for the above | harness | hard-rule | harness (wire `reason_code` into output) | 1 (emit) |

## C. `supervisor/gates/continue_gate.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 13 | Escalation precedence in `decide()` (L18) | Escalation runs before admin-only guard | harness | hard-rule | harness | — |
| 14 | ATTACHED admin-only RE_INJECT (L33) | Keep `ATTACHED` until execution evidence | semantic | heuristic | driven by `progress_class` + `evidence_scope`; heuristic = compat fallback | 3 |
| 15 | Soft-confirmation shortcut (L47) | CONTINUE without human pause on affirmative | semantic | regex | skill/protocol advisory; small fallback | 2–3 |
| 16 | Judge fallback (L71) | LLM judge for ambiguous cases | semantic | fallback judge | optional judge, rare fallback | 3–4 |

## D. `supervisor/gates/branch_gate.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 17 | `confidence_threshold` (L9) | 0.75 floor for branch selection | mechanism | hard-rule | harness | — |
| 18 | `BranchGate.decide()` (L13) | Route decision node | mechanism | hard-rule + judge | harness | — |

## E. `supervisor/gates/finish_gate.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 19 | All-steps-done check (L34) | Structural finish check | mechanism | hard-rule | harness | — |
| 20 | Verification pass check (L46) | `verification.ok == True` | mechanism | structured | harness | — |
| 21 | Git cleanliness check (L51) | `git status --porcelain` | mechanism | mechanism | harness | — |
| 22 | Forbidden-states check (L74) | `test_failing` / `uncommitted_changes` negation | mechanism | hard-rule | harness | — |
| 23 | Required-evidence substring (L84) | String substring match on `evidence[]` | semantic | prose-match | normalizer consumes structured evidence where possible; narrow fallback | 3 |
| 24 | `_review_requirement_met()` (L16) | `must_review_by` in `completed_reviews` | mechanism | structured | harness | — |

## F. `supervisor/gates/supervision_policy.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 25 | `DEFAULT_FAILURE_THRESHOLD` (L11) | 3 consecutive failures before mode escalation | mechanism | hard-rule | harness | — |
| 26 | `determine()` (L28) | Risk × trust × failure → policy mode | mechanism | hard-rule | harness | — |

## G. `supervisor/loop.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 27 | `MAX_RE_INJECTS` (L55) | Attach-boundary re-inject budget cap | mechanism | hard-rule | harness | — |
| 28 | `preserve_state` set (L90) | Prevent GATING re-entry during RECOVERY | mechanism | hard-rule | harness | — |
| 29 | Explicit `status == "blocked"` (L122) | Hardcoded status escalation | semantic | structured status | harness wire up to `escalation_class=business` + `reason_code=esc.blocked` | 3 |
| 30 | ATTACHED-boundary guard (L160) | Admin-only + status + escalation precedence | semantic | heuristic | driven by `progress_class` + `evidence_scope`; heuristic = compat fallback | 3 |
| 31 | step_done / workflow_done short-circuits (L187, L196) | Deterministic advance on structured status | mechanism | structured | harness | — |
| 32 | `apply_decision()` state dispatch (L219) | Central decision → state transition | mechanism | hard-rule | harness | — |
| 33 | RE_INJECT budget exhaustion (L246) | Pause with `pause_class=recovery` after `MAX_RE_INJECTS` | mechanism | hard-rule | harness; stable `reason_code=rec.reinjection_exhausted` | 1 (emit) |
| 34 | RETRY budget exhaustion (L262) | Pause with `pause_class=recovery` | mechanism | hard-rule | harness; `reason_code=rec.retry_budget_exhausted` | 1 (emit) |
| 35 | `_classify_gate_escalation()` (L452) | Keyword-match reason → pause_class | semantic | prose-match | replace with structured `escalation_class` → `pause_class` lookup | 3 |
| 36 | Delivery ack timeout handler (L686) | 60s ack timeout → recovery | mechanism | mechanism | harness; `reason_code=rec.delivery_timeout` | 1 (emit) |
| 37 | Idle timeout handler (L733) | Configurable idle → recovery | mechanism | mechanism | harness; `reason_code=rec.idle_timeout` | 1 (emit) |
| 38 | Node mismatch counter (L814) | 5 persisted mismatches → recovery | mechanism | hard-rule | harness; `reason_code=rec.node_mismatch_persisted` | 1 (emit) |
| 39 | Observation-only rebinding (L786) | Read-only surfaces skip mismatch counter | mechanism | hard-rule | harness | — |

## H. `supervisor/pause_summary.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 40 | `PAUSE_CLASSES` (L7) | Enum for 4 pause classes | mechanism | constant | harness | — |
| 41 | `pause_class()` normalizer (L28) | Pick latest-escalation class | mechanism | structured | harness | — |
| 42 | `is_waiting_for_review()` (L43) | Check `pause_class=review` or `reason.startswith("requires review by:")` | semantic | prose-match | replace prose fallback with `reason_code=esc.review_required` | 3 |
| 43 | `status_reason()` (L50) | Projection of top_state / delivery_state / node into prose | semantic | prose-match | UX-only; harness but switch inputs to structured state | — |
| 44 | `next_action()` (L78) | CLI suggestion per pause_class / top_state | UX | prose-match | harness UX | — |

## I. `supervisor/operator/session_index.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 45 | `_LIVE_STATES`, `_COMPLETED_STATES`, `_ACTIONABLE_ORPHAN_STATES` (L43) | State categorization | mechanism | constant | harness | — |
| 46 | `_derive_tag()` (L211) | Dashboard tag derivation | UX | hard-rule | harness | — |
| 47 | `_liveness()` (L239) | Registry + state-based ownership | mechanism | hard-rule | harness | — |

## J. `supervisor/interventions.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 48 | `maybe_plan()` outer (L18) | Recipe dispatcher | harness | prose-match | harness; consume `reason_code` not prose | 3 |
| 49 | Blocked-checkpoint bypass (L32) | Skip auto-recovery when `status=blocked` | semantic | structured status | harness | — |
| 50 | Node-mismatch recipe (L36) | "node mismatch persisted" → focused re-inject | semantic | prose-match | consume `reason_code=rec.node_mismatch_persisted` | 3 |
| 51 | Delivery-timeout recipe (L55) | "no checkpoint received within delivery timeout" | semantic | prose-match | consume `reason_code=rec.delivery_timeout` | 3 |
| 52 | Idle-timeout recipe (L74) | "idle timeout" | semantic | prose-match | consume `reason_code=rec.idle_timeout` | 3 |
| 53 | Inject-failed recipe (L91) | "injection failed" / "inject failed" | semantic | prose-match | consume `reason_code=rec.inject_failed` | 3 |
| 54 | Retry-budget recipe (L105) | "retry budget exhausted" | semantic | prose-match | consume `reason_code=rec.retry_budget_exhausted` | 3 |

## K. `supervisor/instructions/composer.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 55 | Policy-mode templates (L26) | Strict / collaborative / directive prose blocks | mechanism | hard-rule | harness | — |
| 56 | First-node checkpoint protocol suffix (L55) | Explicit first-checkpoint contract reminder | skill/protocol | hard-rule | skill/protocol | 2 |

## L. `supervisor/protocol/checkpoints.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 57 | `CHECKPOINT_ALLOWED_STATUSES` (L6) | 4 valid status values | mechanism | constant | harness | — |
| 58 | `sanitize_checkpoint_payload()` (L57) | Validation + truncation | mechanism | hard-rule | extend into normalizer (adds v1/v2 fork + semantic field parse) | 1–2 |
| 59 | `checkpoint_example_block()` (L20) | Prompt suffix | skill/protocol | hard-rule | extend with v2 fields | 2 |

## M. `supervisor/adapters/transcript_adapter.py`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 60 | `CHECKPOINT_RE` (L12) | `<checkpoint>...</checkpoint>` block regex | mechanism | regex | harness | — |
| 61 | `parse_checkpoints()` (L14) | YAML / line-fallback parse | mechanism | hard-rule | harness; feed into normalizer entrypoint | 1 |
| 62 | `_parse_lines()` (L64) | Manual fallback when YAML fails | mechanism | hard-rule | harness | — |

## N. `supervisor/llm/prompts/checkpoint_protocol.txt`

| # | Site | Purpose | Category | Mode | Target home | Slice |
| --- | --- | --- | --- | --- | --- | --- |
| 63 | Checkpoint format spec | Mandatory fields schema in prose | skill/protocol | hard-rule | skill/protocol; add `checkpoint_schema_version` + semantic fields | 2 |
| 64 | Status semantics | `working` / `blocked` / `step_done` / `workflow_done` | skill/protocol | hard-rule | skill/protocol | — |
| 65 | First-checkpoint requirement | Must cite concrete current-node work | skill/protocol | hard-rule | skill/protocol | — |

---

## Cross-cutting observations

- The prose-matching hot spots that MUST be defused (primary drift risk):
  rows 2, 3, 4, 6, 35, 42, 50–54.
- `sanitize_checkpoint_payload` (row 58) is the existing single entrypoint
  for raw → safe checkpoint dict — Slice 1 extends it with
  `checkpoint_schema_version` parsing; Slice 2 extends again to parse the
  new semantic fields; downstream callers continue to consume via this
  one path, matching the **canonical normalization layer** rule in
  decision C of the repartitioning doc.
- Rows 33, 34, 36, 37, 38 emit recovery reasons today as prose. Slice 1
  pairs each with a stable `reason_code` without changing consumers;
  Slice 3 switches `supervisor/interventions.py` to route on the code.
- Row 10 (`_ESCALATION_REASON`) is the paired emitter for escalation
  classes. Slice 1 extends it to carry a `reason_code` alongside the
  prose; Slice 3 makes the code primary.
- Nothing in Slices F (`supervision_policy`) or I (`session_index`) is
  scheduled to move — they are already structured / mechanism.
