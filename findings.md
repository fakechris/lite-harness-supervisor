# Findings & Decisions

## Requirements
- Read the local `lite-harness-supervisor` project, not just the external repo in isolation.
- Study `https://github.com/slaveOftime/open-relay` in detail, including real code paths.
- Explain the mechanism, not just the README pitch.
- Convert the comparison into concrete inspiration for this project.

## Research Findings
- `thin-supervisor status` shows no active supervisor run: `run_e87be662caaf` is `COMPLETED`, so the checkpoint protocol is not currently active.
- The local project has a compact supervisor architecture centered on `supervisor/loop.py`, `supervisor/domain/state_machine.py`, gates, verifiers, adapters, and a daemon layer.
- The local project's effective runtime contract is: read terminal output from tmux, parse explicit agent checkpoints, decide `continue/verify/retry/branch/escalate/finish`, run deterministic verifiers in the agent CWD, then inject the next instruction only after persisting state.
- `open-relay` (`oly`) lives one layer lower. It daemonizes PTY-backed processes, persists output and metadata, exposes CLI + HTTP + WebSocket surfaces, and lets humans or agents attach/send/resize without owning the original terminal.
- `open-relay` keeps both byte-level and screen-level state: a canonical filtered PTY byte stream is written to disk, while a live `vt100::Parser` snapshot is maintained in memory so new clients can start from the rendered screen instead of replaying the entire log.
- `open-relay` treats prompt detection as an attention signal, not a control signal. It combines silence windows, regex prompt patterns, and future LLM checks into `input_needed`, then notifies or unblocks `logs --wait-for-prompt`.
- The two projects are complementary rather than interchangeable: `lite-harness-supervisor` orchestrates agent plans through explicit checkpoints and verification, while `open-relay` provides durable interactive execution and remote intervention primitives.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Use connector/web evidence for the external GitHub repo and local filesystem reads for the current project | Keeps external facts sourced while allowing full local code inspection |
| Treat `open-relay` as adjacent infrastructure, not as a replacement architecture | Its abstraction boundary is PTY session hosting, while this project's boundary is workflow supervision |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Plugin cache hash in the skill list did not match the active local path | Located the valid path in `/Users/chris/.codex/plugins/cache/openai-curated/github/...` |

## Resources
- Local repo root: `/Users/chris/workspace/lite-harness-supervisor`
- External repo: `https://github.com/slaveOftime/open-relay`
- GitHub skill: `/Users/chris/.codex/plugins/cache/openai-curated/github/fb0a18376bcd9f2604047fbe7459ec5aed70c64b/skills/github/SKILL.md`
- Local core files: `supervisor/loop.py`, `supervisor/storage/state_store.py`, `supervisor/gates/continue_gate.py`, `supervisor/gates/branch_gate.py`, `supervisor/gates/finish_gate.py`, `supervisor/terminal/adapter.py`
- open-relay core files: `/tmp/open-relay/src/session/runtime.rs`, `/tmp/open-relay/src/session/store.rs`, `/tmp/open-relay/src/session/pty.rs`, `/tmp/open-relay/src/daemon/rpc_attach.rs`, `/tmp/open-relay/src/notification/mod.rs`, `/tmp/open-relay/src/http/ws.rs`, `/tmp/open-relay/src/node/registry.rs`

## Visual/Browser Findings
- None yet.

---
*Update this file after every 2 view/browser/search operations*
