"""Stop-hook handler for observation-only (JSONL) surfaces.

When the agent is running in JSONL observation mode, the supervisor cannot
inject directly into a terminal. Instead, it writes the next instruction to
a file and waits for the agent's Stop hook to pick it up:

    .supervisor/runtime/instructions/<session_id>.json       — pending instruction
    .supervisor/runtime/instructions/<session_id>.delivered.json — ACK

The hook handler (`thin-supervisor hook stop`) runs inside the agent
process on stop. It reads the pending instruction, emits it to stderr, writes
the ACK, and exits 2 so the agent (Claude Code / Codex convention) treats the
stderr text as a "reason to keep going" message.

If no instruction is pending but a supervisor run is still active, the hook
falls through to a generic "continue working" message (legacy `check-active.sh`
behavior) — this prevents the agent from terminating mid-run.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INSTRUCTION_DIR = Path(".supervisor/runtime/instructions")
STATE_FILE = Path(".supervisor/runtime/state.json")
PID_FILE = Path(".supervisor/runtime/supervisor.pid")

INSTRUCTION_SCHEMA = "instruction.v1"
ACK_SCHEMA = "instruction_ack.v1"

TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "ABORTED"})


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def instruction_path(session_id: str, root: Path | None = None) -> Path:
    base = (root or Path.cwd()) / INSTRUCTION_DIR
    return base / f"{session_id}.json"


def ack_path(session_id: str, root: Path | None = None) -> Path:
    base = (root or Path.cwd()) / INSTRUCTION_DIR
    return base / f"{session_id}.delivered.json"


def write_instruction(
    session_id: str,
    *,
    instruction_id: str,
    content: str,
    run_id: str = "",
    node_id: str = "",
    root: Path | None = None,
) -> Path:
    """Write a pending instruction for the hook to deliver.

    Returns the instruction file path. Overwrites any previous pending
    instruction for the same session (the loop owns that file).
    """
    if not session_id:
        raise ValueError("session_id must not be empty")
    if not instruction_id:
        raise ValueError("instruction_id must not be empty")
    if content is None:
        raise ValueError("content must not be None")

    payload = {
        "schema": INSTRUCTION_SCHEMA,
        "instruction_id": instruction_id,
        "run_id": run_id,
        "node_id": node_id,
        "content": content,
        "content_sha256": _sha256(content),
        "written_at": _now_iso(),
    }
    path = instruction_path(session_id, root=root)
    _atomic_write(path, payload)
    return path


def read_instruction(session_id: str, root: Path | None = None) -> dict | None:
    path = instruction_path(session_id, root=root)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != INSTRUCTION_SCHEMA:
        return None
    return data


def read_ack(session_id: str, root: Path | None = None) -> dict | None:
    path = ack_path(session_id, root=root)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != ACK_SCHEMA:
        return None
    return data


def write_ack(
    session_id: str,
    *,
    instruction_id: str,
    content_sha256: str,
    root: Path | None = None,
) -> Path:
    payload = {
        "schema": ACK_SCHEMA,
        "instruction_id": instruction_id,
        "content_sha256": content_sha256,
        "session_id": session_id,
        "delivered_at": _now_iso(),
    }
    path = ack_path(session_id, root=root)
    _atomic_write(path, payload)
    return path


def _now_iso() -> str:
    # Second-precision UTC ISO8601 with Z suffix.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class HookResult:
    exit_code: int
    stderr: str
    delivered_instruction_id: str = ""


def _supervisor_active(root: Path | None = None) -> bool:
    """Best-effort check: is a supervisor run active in this worktree.

    Mirrors the legacy `check-active.sh` logic: daemon alive + state.json
    exists + top_state is non-terminal.
    """
    base = root or Path.cwd()
    pid_file = base / PID_FILE
    state_file = base / STATE_FILE

    if not pid_file.exists() or not state_file.exists():
        return False

    try:
        pid_text = pid_file.read_text(encoding="utf-8").strip()
        pid = int(pid_text)
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False

    try:
        with state_file.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    top_state = str(state.get("top_state", "")).upper()
    if not top_state or top_state in TERMINAL_STATES:
        return False
    return True


def run_stop_hook(session_id: str, root: Path | None = None) -> HookResult:
    """Handle a Stop-hook invocation.

    Returns a HookResult. The CLI is responsible for translating it into
    stderr/exit code.
    """
    if not session_id:
        return HookResult(exit_code=0, stderr="")

    pending = read_instruction(session_id, root=root)
    if pending:
        instruction_id = str(pending.get("instruction_id") or "")
        content = str(pending.get("content") or "")
        content_hash = str(pending.get("content_sha256") or _sha256(content))

        ack = read_ack(session_id, root=root)
        already_delivered = (
            ack is not None
            and ack.get("instruction_id") == instruction_id
            and ack.get("content_sha256") == content_hash
        )

        if not already_delivered:
            write_ack(
                session_id,
                instruction_id=instruction_id,
                content_sha256=content_hash,
                root=root,
            )
            return HookResult(
                exit_code=2,
                stderr=content,
                delivered_instruction_id=instruction_id,
            )
        # Already delivered: fall through to "run active?" check below so we
        # don't spam the same instruction every time the agent stops.

    if _supervisor_active(root=root):
        return HookResult(
            exit_code=2,
            stderr=(
                "Supervisor run is active. Continue working on the current step; "
                "the supervisor will emit the next instruction when ready."
            ),
        )

    return HookResult(exit_code=0, stderr="")
