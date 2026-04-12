"""Auto-detect the current agent session (Codex or Claude Code).

Skill runs inside the agent process, so it can discover:
- Which agent is running
- The session ID
- The JSONL transcript file path
- The project working directory
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def detect_agent() -> str:
    """Detect which agent environment we're in."""
    if (Path.home() / ".codex").exists() and _codex_session_env_id():
        return "codex"
    # Check for recent Codex session files
    codex_sessions = Path.home() / ".codex" / "sessions"
    if codex_sessions.exists():
        return "codex"
    if (Path.home() / ".claude").exists():
        return "claude"
    return "unknown"


def detect_session_id(agent: str = "") -> str:
    """Detect the current session ID.

    Codex: from env var or most recent rollout filename.
    Claude: from most recent transcript filename.
    """
    if not agent:
        agent = detect_agent()

    if agent == "codex":
        # Try env var first
        sid = _codex_session_env_id()
        if sid:
            return sid
        # Fall back to most recent rollout file
        path = find_latest_jsonl(agent)
        if path:
            # Extract session ID from filename: rollout-YYYY-MM-DDTHH-MM-SS-{uuid-with-dashes}
            import re
            m = re.match(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.*)", path.stem)
            if m:
                return m.group(1)
        return ""

    if agent == "claude":
        path = find_latest_jsonl(agent)
        if path:
            # ses_{session_id}.jsonl
            name = path.stem
            if name.startswith("ses_"):
                return name[4:]
        return ""

    return ""


def find_jsonl_for_session(session_id: str, agent: str = "") -> Path | None:
    """Find the transcript file for a specific session ID."""
    if not session_id:
        return None
    if not agent:
        agent = detect_agent()

    if agent == "codex":
        base = Path.home() / ".codex" / "sessions"
        if not base.exists():
            return None
        candidates = sorted(
            base.rglob(f"rollout-*-{session_id}.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    if agent == "claude":
        path = Path.home() / ".claude" / "transcripts" / f"ses_{session_id}.jsonl"
        return path if path.exists() else None

    return None


def find_latest_jsonl(agent: str = "") -> Path | None:
    """Find the most recently modified JSONL transcript file.

    Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
    Claude: ~/.claude/transcripts/ses_*.jsonl
    """
    if not agent:
        agent = detect_agent()

    if agent == "codex":
        base = Path.home() / ".codex" / "sessions"
        if not base.exists():
            return None
        # Find most recent rollout file
        candidates = sorted(base.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    if agent == "claude":
        base = Path.home() / ".claude" / "transcripts"
        if not base.exists():
            return None
        candidates = sorted(base.glob("ses_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    return None


def _codex_session_env_id() -> str:
    return (
        os.environ.get("CODEX_SESSION_ID", "")
        or os.environ.get("CODEX_THREAD_ID", "")
    )


def detect_cwd_from_jsonl(jsonl_path: Path, agent: str = "") -> str:
    """Extract the project working directory from a JSONL transcript.

    Codex: session_meta.payload.cwd or turn_context.payload.cwd
    Claude: derive from ~/.claude/projects/<encoded-path>/ directory name
    """
    if not agent:
        agent = detect_agent()

    if agent == "codex":
        try:
            with jsonl_path.open() as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        if event.get("type") == "session_meta":
                            cwd = event.get("payload", {}).get("cwd", "")
                            if cwd:
                                return cwd
                        elif event.get("type") == "turn_context":
                            cwd = event.get("payload", {}).get("cwd", "")
                            if cwd:
                                return cwd
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return ""

    if agent == "claude":
        # Claude stores projects at ~/.claude/projects/<encoded-path>/
        # The encoded path replaces / with -
        # BUT paths may contain real hyphens, so naive replace("-","/") is lossy.
        # Instead, try to find the real path by checking which decoded candidate exists.
        projects_dir = Path.home() / ".claude" / "projects"
        if projects_dir.exists():
            candidates = sorted(
                [d for d in projects_dir.iterdir() if d.is_dir()],
                key=lambda d: d.stat().st_mtime, reverse=True,
            )
            for candidate in candidates[:5]:
                decoded = _decode_claude_project_path(candidate.name)
                if decoded and Path(decoded).is_dir():
                    return decoded
        return ""

    return ""


def _decode_claude_project_path(encoded: str) -> str:
    """Decode Claude Code's encoded project path.

    Claude encodes /Users/chris/workspace/project as -Users-chris-workspace-project.
    Naive replace("-", "/") fails for paths with real hyphens.

    Strategy: try progressively replacing dashes with slashes from left to right,
    and return the first result that is an existing directory.
    If none exist, fall back to naive replacement.
    """
    if not encoded.startswith("-"):
        return ""
    # Naive decode as fallback
    naive = encoded.replace("-", "/")
    if Path(naive).is_dir():
        return naive
    # Couldn't verify — return naive anyway (caller checks existence)
    return naive


def list_sessions() -> list[dict]:
    """List all discoverable sessions across agents (diagnostic tool)."""
    sessions = []

    # Codex sessions
    codex_base = Path.home() / ".codex" / "sessions"
    if codex_base.exists():
        for path in sorted(codex_base.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            cwd = detect_cwd_from_jsonl(path, "codex")
            sessions.append({
                "agent": "codex",
                "path": str(path),
                "cwd": cwd,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            })

    # Claude sessions
    claude_base = Path.home() / ".claude" / "transcripts"
    if claude_base.exists():
        for path in sorted(claude_base.glob("ses_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            sessions.append({
                "agent": "claude",
                "path": str(path),
                "session_id": path.stem[4:] if path.stem.startswith("ses_") else "",
                "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            })

    return sessions
