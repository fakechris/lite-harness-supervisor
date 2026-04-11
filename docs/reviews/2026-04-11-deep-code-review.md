# Deep Code Review: thin-supervisor

> **Date**: 2026-04-11
> **Scope**: 69 files, +6800 lines across 40+ commits (f8585df..0ef8874)
> **Tests**: 172 passed ✅

---

## Table of Contents

- [Round 1: Primitives, Surfaces, Timing](#round-1)
  - [1.1 First-class Primitives Review](#11-first-class-primitives-review)
  - [1.2 Multi-Surface Access Methods](#12-multi-surface-access-methods)
  - [1.3 Timing & Race Conditions](#13-timing--race-conditions)
  - [1.4 Findings](#14-round-1-findings)
- [Scenario Matrix: Full Flow Trace](#scenario-matrix)
  - [Surface × Agent × Phase Matrix](#surface--agent--phase-matrix)
  - [Per-Scenario Analysis](#per-scenario-analysis)
  - [Root Causes](#root-causes-6-items)
- [Round 1 Fix Verification](#round-1-fix-verification)
- [Round 2: Post-Fix Full Re-trace](#round-2)
  - [Scenario A: tmux + Codex (golden path)](#scenario-a-tmux--codex)
  - [Scenario B: Verification Failure + Retry](#scenario-b-verification-failure--retry)
  - [Scenario C: open_relay + Codex](#scenario-c-open_relay--codex)
  - [Scenario D: jsonl + Codex (observation-only)](#scenario-d-jsonl--codex)
  - [Scenario E: Daemon Crash Recovery](#scenario-e-daemon-crash-recovery)
  - [Scenario F: Concurrent Runs](#scenario-f-concurrent-runs)
  - [Scenario G: Read Guard Correctness](#scenario-g-read-guard-correctness)
  - [Scenario H: Dedup Correctness](#scenario-h-dedup-correctness)
  - [Round 2 Findings](#round-2-findings)
- [Overall Assessment](#overall-assessment)

---

<a id="round-1"></a>
## Round 1: Primitives, Surfaces, Timing

### 1.1 First-class Primitives Review

Six-layer architecture with well-defined first-class objects:

| Object | File | Key Fields | Status |
|--------|------|------------|--------|
| Checkpoint | `domain/models.py` | status, current_node, summary, run_id, checkpoint_seq, checkpoint_id, surface_id | ✅ Stable |
| SupervisorDecision | `domain/models.py` | decision_id, decision, reason, confidence, gate_type, triggered_by_seq, triggered_by_checkpoint_id | ✅ Stable |
| HandoffInstruction | `domain/models.py` | instruction_id, content, node_id, triggered_by_decision_id, trigger_type | ✅ Stable |
| AcceptanceContract | `domain/models.py` | goal, required_evidence, forbidden_states, risk_class, must_review_by | ⚠️ Maturing |
| WorkerProfile | `domain/models.py` | worker_id, provider, model_name, role, trust_level | ⚠️ Maturing |
| SupervisionPolicy | `domain/models.py` | mode, reason, risk_class, failure_threshold | ⚠️ Maturing |
| RoutingDecision | `domain/models.py` | routing_id, target_type, scope, reason, triggered_by_decision_id | ⚠️ Maturing |
| SessionRun | `domain/session.py` | state + acceptance + worker + policy + routing_history | ⚠️ Maturing |

**Causality chain**: `Checkpoint(seq=N) → SupervisorDecision(triggered_by_seq=N, triggered_by_checkpoint_id=X) → HandoffInstruction(triggered_by_decision_id=Y)`

### 1.2 Multi-Surface Access Methods

Three surfaces sharing `SessionAdapter` protocol:

| Surface | `read()` | `inject()` | `current_cwd()` | `is_observation_only` |
|---------|----------|------------|------------------|----------------------|
| `TerminalAdapter` (tmux) | `tmux capture-pane` (snapshot) | `tmux send-keys` (sync) | `tmux display-message` (precise) | False |
| `OpenRelaySurface` (oly) | `oly logs` (cumulative, hash-deduped) | `oly send` (sync) | `oly ls --json` (startup cwd only) | False |
| `JsonlObserver` | byte-offset tail of JSONL file (incremental) | write file (async, no delivery guarantee) | JSONL metadata or fallback | True |

### 1.3 Timing & Race Conditions

- **Global registry**: `acquire_pane_lock` uses `O_CREAT|O_EXCL` for atomic creation ✅
- **State store**: atomic write via `tempfile.mkstemp` + `os.replace` ✅
- **Daemon threading**: two-phase reap (collect under lock, join outside lock) ✅
- **JSONL offset**: byte-level tracking + partial line protection ✅
- **Checkpoint dedup**: seq-based + content-based dual dedup with reset tolerance ✅

### 1.4 Round 1 Findings

| # | Severity | File | Issue |
|---|----------|------|-------|
| 1 | 🔴 High | `loop.py` | `last_injected_attempt` was a local variable, lost on daemon crash → spurious injection replay |
| 2 | 🔴 High | `jsonl_observer.py` | `inject()` wrote to a fixed path — concurrent runs would overwrite each other's instructions |
| 3 | 🔴 High | `server.py` | `_reap_finished` called `thread.join(timeout=2)` inside global lock, blocking IPC; zombie threads on timeout |
| 4 | 🔴 High | `server.py` | `_do_register` always creates new `run_id` + `run_dir` — cannot resume paused runs |
| 5 | 🟡 Medium | `models.py` | `SupervisorDecision.triggered_by_seq` breaks causality when agent omits `checkpoint_seq` |
| 6 | 🟡 Medium | `jsonl_observer.py` | File rotation detection relies on `file_size < offset` — misses fast-growing rotated files |
| 7 | 🟡 Medium | `adapter.py` | `_confirm_injection` timeout too short (1s) — false positives on slow models |
| 8 | 🟢 Low | `SKILL.md` (both) | Still instructs agent to run `thin-supervisor status` for `run_id` — unnecessary |
| 9 | 🟢 Low | `finish_gate.py` | `git status --porcelain` called twice redundantly |
| 10 | 🟢 Low | `finish_gate.py` | `conditional_workflow` set math in `require_all_steps_done` is a no-op |

---

<a id="scenario-matrix"></a>
## Scenario Matrix: Full Flow Trace

### Surface × Agent × Phase Matrix

Five phases traced per scenario:

1. **Attach** — How skill/script starts the supervisor
2. **Observe** — How supervisor reads agent output
3. **Parse** — Extracting checkpoints from output
4. **Verify** — Running verification commands (cwd correctness)
5. **Inject** — Sending instructions back to agent

### Per-Scenario Analysis

#### tmux + Codex (Golden Path)

| Phase | Flow | Status |
|-------|------|--------|
| Attach | `attach.sh` → `bridge id` → `$TMUX_PANE` → `run register` | ✅ |
| Observe | `tmux capture-pane` | ✅ |
| Parse | regex on terminal text | ✅ |
| Verify | `tmux display-message` → precise cwd | ✅ |
| Inject | `tmux send-keys` → sync delivery | ✅ |

#### tmux + Claude Code

Same as tmux + Codex except inject relies on Claude Code accepting `send-keys` as user input. Works in practice but is an implicit dependency. ⚠️

#### open_relay + Codex

| Phase | Flow | Status (before fix) |
|-------|------|---------------------|
| Attach | `attach.sh` → `bridge id` → fails (not in tmux) | ❌ |
| Observe | `oly logs` → cumulative, returns all history including old checkpoints | ⚠️ |
| Parse | Old checkpoints re-parsed each time | ⚠️ |
| Verify | `oly ls --json` → only startup cwd, not runtime cwd | ⚠️ |
| Inject | `oly send` → works | ✅ |

#### jsonl + Codex

| Phase | Flow | Status (before fix) |
|-------|------|---------------------|
| Attach | `attach.sh` → `bridge id` → fails | ❌ |
| Observe | byte-offset tail of JSONL | ⚠️ |
| Parse | Checkpoint may span multiple JSONL events | ⚠️ |
| Verify | cwd from JSONL metadata or empty → fallback to daemon cwd | ⚠️ |
| Inject | write to fixed file path, no delivery mechanism | ❌ |

### Root Causes (6 items)

1. **`attach.sh` hardcoded tmux** — No surface awareness. Only tmux `bridge id` was called.
2. **`--pane` parameter semantic overload** — Same CLI flag for tmux pane ID, oly session ID, and JSONL path. No validation of format vs surface_type.
3. **`inject()` semantic mismatch** — tmux/relay are synchronous push. JSONL is async file write with no consumer.
4. **`read()` return semantics differ** — tmux returns screen snapshot (bounded, repeating). oly returns cumulative log (growing). JSONL returns incremental events (offset-based). Same dedup logic applied to all three.
5. **cwd acquisition paths diverge** — tmux has precise runtime cwd; oly has startup-only cwd; JSONL has metadata-only cwd; fallback was daemon process cwd (wrong).
6. **SKILL descriptions tmux-only** — No mention of open_relay or JSONL attach paths.

---

<a id="round-1-fix-verification"></a>
## Round 1 Fix Verification

All 6 root causes addressed in PRs #21 and #22:

| Root Cause | Fix | Verified |
|------------|-----|----------|
| `attach.sh` hardcoded tmux | Reads `config.yaml` `surface_type`, branches to tmux/jsonl/open_relay | ✅ |
| `--pane` semantic overload | `_resolve_target_and_surface()` with format validation warnings; `--surface` flag; `surface_type` in IPC | ✅ |
| JSONL inject broken loop | `is_observation_only` property; `_inject_or_pause` continues observing without pausing | ✅ |
| cwd fallback wrong | `_get_cwd()` falls back to `state.workspace_root` instead of daemon cwd | ✅ |
| oly cumulative read dedup | `OpenRelaySurface.read()` uses md5 hash to return empty on unchanged content | ✅ |
| Reaper lock contention | Three-phase reap: collect candidates → join outside lock → remove under lock | ✅ |

Additional fixes:
- `last_injected_attempt` persisted in `SupervisorState` ✅
- `triggered_by_checkpoint_id` added to `SupervisorDecision` ✅
- `_confirm_injection` timeout increased to 5s (10×0.5s) ✅
- `finish_gate` git status deduplicated via `git_dirty` variable ✅
- JSONL inject path namespaced by session ID ✅

---

<a id="round-2"></a>
## Round 2: Post-Fix Full Re-trace

Complete line-by-line trace of every scenario through current code.

<a id="scenario-a-tmux--codex"></a>
### Scenario A: tmux + Codex (Golden Path)

**Attach**: `attach.sh` → `grep surface_type` → tmux → `bridge id` → `run register --pane %42` → `_resolve_target_and_surface` → daemon `_do_register(surface_type="tmux")` → `create_surface("tmux", "%42")` → ✅

**Init inject**: READY → RUNNING → `terminal.read()` → `_read_guard.add` → `parse_checkpoint` → None → `composer.build(init)` → save before inject → `_inject_or_pause` → `_require_read` passes (guard set by read) → `tmux send-keys` → `_confirm_injection` (10×0.5s) → ✅

**Main loop**: `read()` → `parse_checkpoint` → seq/content dedup → node match → event → gate → decision → verify → `_get_cwd` (tmux precise) → `CommandVerifier(cwd=project)` → `apply_verification` → advance → inject → ✅

**All phases verified correct.** ✅

<a id="scenario-b-verification-failure--retry"></a>
### Scenario B: Verification Failure + Retry

Traced retry budget exhaustion:
- `apply_verification(ok=False)` → `current_attempt++` → check `per_node` limit
- Inject on retry: `new_retry = (attempt > 0 and attempt != last_injected_attempt)` → True
- `composer.build` appends "Previous verification failed: ..." → ✅
- Budget exhausted (attempt >= per_node) → `PAUSED_FOR_HUMAN` → loop exits → ✅

<a id="scenario-c-open_relay--codex"></a>
### Scenario C: open_relay + Codex

**Attach**: `attach.sh` → open_relay case → prints manual instructions → exit 1. User must manually: `run register --spec ... --pane <oly-id> --surface open_relay` → ⚠️ Expected behavior.

**Read dedup**: First `read()` sets hash. Subsequent `read()` with identical content → returns `""` → `parse_checkpoint("")` → None → sleep. Content changes → new hash → returns text → ✅

**Injected text echo**: `oly send` puts instruction in terminal. Next `oly logs` includes it. `parse_checkpoint` scans it — safe because `InstructionComposer` never outputs `<checkpoint>` tags. **Fragile implicit dependency.**

<a id="scenario-d-jsonl--codex"></a>
### Scenario D: jsonl + Codex (Observation-Only)

**Attach**: `attach.sh` → jsonl case → `session jsonl` → `find_latest_jsonl` → register with `--surface jsonl` → ✅

**Init inject**: `_inject_or_pause` → `is_observation_only=True` → writes file, logs warning, returns True (continues observing) → ✅

**Observation loop**: `read()` → byte-offset tail → `_extract_text` per event → join → `parse_checkpoint` → ⚠️ See finding #1

**Post-verify advance**: supervisor moves to step_2 → agent still reports step_1 → node mismatch → 5× → PAUSED_FOR_HUMAN → ⚠️ See finding #2

<a id="scenario-e-daemon-crash-recovery"></a>
### Scenario E: Daemon Crash Recovery

`run register` after crash → new `run_id` + new `run_dir` → `load_or_init` in empty dir → starts from step_1. Old state orphaned in `.supervisor/runtime/runs/run_oldxxx/`. No `resume` command exists. **Still broken.** See finding #3.

<a id="scenario-f-concurrent-runs"></a>
### Scenario F: Concurrent Runs

Two runs on different panes in same workspace. Independent `StateStore`, independent `RunEntry.stop_event`. `subprocess.run(cwd=cwd)` is thread-safe. **Only risk**: concurrent verify commands on shared git repo may interfere (not a supervisor bug, but an operational concern).

<a id="scenario-g-read-guard-correctness"></a>
### Scenario G: Read Guard Correctness

Traced all code paths:
- READY → read → inject: guard set by read, consumed by inject ✅
- While loop → read → ... → inject: guard set each iteration ✅
- READY with existing checkpoint → skip inject → while loop → inject: guard from READY-phase read survives ✅

**All read guard paths verified correct.** ✅

<a id="scenario-h-dedup-correctness"></a>
### Scenario H: Dedup Correctness

- seq > 0, seq ≤ state.checkpoint_seq, gap < 100 → skip ✅
- seq > 0, seq > state.checkpoint_seq → process ✅
- seq = 0 → skip seq check, fall through to content dedup ✅
- Content dedup: 4-field match (status, current_node, summary, checkpoint_seq) ✅
- Edge case: two different checkpoints in same `capture-pane` window → only last one processed (by design, via `matches[-1]`) ✅

---

<a id="round-2-findings"></a>
### Round 2 Findings

| # | Severity | Component | Issue | Impact |
|---|----------|-----------|-------|--------|
| 1 | 🔴 High | `jsonl_observer.py` | **JSONL checkpoint cross-event split** — If agent streams output across multiple JSONL events, `<checkpoint>` and `</checkpoint>` may land in different events. If these events span two `read()` calls (byte offset boundary), the checkpoint regex never matches. The `_extract_text` + `"\n".join()` reassembly only works within a single `read()` batch. **Fix**: maintain a cross-read text buffer in `JsonlObserver` that carries over incomplete checkpoint blocks. | JSONL mode may silently miss checkpoints |
| 2 | 🔴 High | `loop.py` + `jsonl_observer.py` | **JSONL observation-only multi-step deadlock** — After verify passes, supervisor advances `state.current_node_id` to step_2. But `inject` in observation-only mode writes a file that agent never reads. Agent continues reporting `current_node: step_1`. After 5 mismatches → `PAUSED_FOR_HUMAN`. **JSONL mode is effectively single-step-only.** **Fix**: either (a) don't advance node in observation-only mode, or (b) add instruction polling to SKILL.md, or (c) document this as a known limitation. | JSONL mode cannot drive multi-step workflows |
| 3 | 🔴 High | `server.py` | **Daemon crash recovery still broken** — `_do_register` always creates a new `run_id` + `run_dir`. No mechanism to discover and resume a paused or crashed run. The state from the old run is preserved on disk but unreachable. **Fix**: add a `resume` action that accepts an existing `run_id` or scans `runs_dir` for matching `spec_path + pane_target`. | Users cannot recover from daemon crashes or resume paused runs |
| 4 | 🟡 Medium | `loop.py` + `transcript_adapter.py` | **tmux fast-checkpoint loss** — `capture-pane` returns a screen snapshot. `parse_checkpoint` takes `matches[-1]`. If two `working` checkpoints are on screen simultaneously, only the last one is processed. Evidence from the first is lost. Acceptable for `step_done` (agent stops after emitting), but `working` evidence accumulation may be incomplete. | Minor evidence loss for rapid `working` checkpoints |
| 5 | 🟡 Medium | `open_relay_surface.py` | **Injected text echo in next read** — `oly send` causes instruction text to appear in `oly logs` output. Currently safe because `InstructionComposer` never emits `<checkpoint>` tags. But this is a fragile implicit dependency — any future template change could cause false checkpoint parsing. **Fix**: add an explicit filter in `TranscriptAdapter.parse_checkpoint` or `OpenRelaySurface.read` to strip supervisor-injected content. | Potential false checkpoint parsing if instruction format changes |
| 6 | 🟡 Medium | `loop.py` | **JSONL init inject is a no-op** — On startup, supervisor writes init instruction to file, but agent has no mechanism to read it. Agent doesn't know supervisor started or which step to begin. If agent's first checkpoint has `current_node` ≠ `spec.first_node_id`, immediate mismatch occurs. | JSONL mode startup may mismatch immediately |
| 7 | 🟢 Low | `state_store.py` | **`_read_last_seq` is O(n)** — Scans entire `session_log.jsonl` to find max seq on resume. For long-running tasks with thousands of events, this may take seconds. | Minor startup latency on large logs |
| 8 | 🟢 Low | `attach.sh` | **Config grep fragility** — `grep "^surface_type:" \| awk '{print $2}'` can be fooled by YAML comments or single-quote wrapping. | Edge case config parsing failure |

---

<a id="overall-assessment"></a>
## Overall Assessment

### What's solid

- **tmux + Codex/Claude golden path**: Fully functional, all phases verified correct
- **Causality chain**: Checkpoint → Decision → Instruction with proper ID linking
- **State persistence**: Atomic writes, crash-safe save-before-inject pattern
- **Dedup logic**: Dual seq+content dedup handles all edge cases correctly
- **Read guard**: All code paths maintain the read-before-write invariant
- **Concurrency**: Thread-safe daemon with proper lock disciplines
- **Supervision policy**: Three-mode engine (strict/collaborative/directive) correctly adapts to worker trust × risk × failures

### What needs work

- **JSONL observation-only mode**: Two architectural issues (#1 cross-event split, #2 multi-step deadlock) make it effectively single-step-only. The mode needs either a buffering fix + instruction polling, or explicit documentation as "monitor-only, not workflow-driving."
- **Crash recovery / resume**: No mechanism exists to resume a paused or crashed run. This affects all surface types.
- **open_relay implicit dependencies**: Injected text echo (#5) is safe today but architecturally fragile.

### Maturity by surface type

| Surface | Workflow driving | Observation | Resume | Production readiness |
|---------|-----------------|-------------|--------|---------------------|
| tmux | ✅ Full | ✅ | ❌ No resume | ⚠️ Ready with caveat |
| open_relay | ✅ Full | ✅ | ❌ No resume | ⚠️ Ready with caveat |
| jsonl | ❌ Single-step only | ⚠️ Cross-event risk | ❌ No resume | 🔴 Not ready for multi-step |
