"""OpenRelaySurface — ExecutionSurface backed by an open-relay (oly) session.

Requires `oly` CLI installed and daemon running.
Connects to an EXISTING session by ID — does not create sessions.
"""
from __future__ import annotations

import json
import shutil
import subprocess


class OpenRelaySurfaceError(RuntimeError):
    pass


class OpenRelaySurface:
    """Adapter for open-relay sessions via the `oly` CLI."""

    def __init__(self, session_id: str):
        if not session_id or not session_id.strip():
            raise OpenRelaySurfaceError("session_id must not be empty")
        self._session_id = session_id.strip()
        self._last_read_hash = ""  # for incremental dedup
        self._pending_echo_filters: list[str] = []

    def read(self, lines: int = 100) -> str:
        """Read recent output, returning only new content since last read."""
        result = self._oly("logs", self._session_id,
                           "--tail", str(lines), "--no-truncate")
        content = self._strip_pending_echoes(result.stdout)

        # Dedup: only return content if it changed since last read
        import hashlib
        content_hash = hashlib.md5(content.encode()).hexdigest()
        if content_hash == self._last_read_hash:
            return ""  # no new content
        self._last_read_hash = content_hash
        return content

    def inject(self, text: str) -> None:
        """Send text + Enter to the oly session."""
        self._oly("send", self._session_id, text, "key:enter")
        if text:
            self._pending_echo_filters.append(text)

    def current_cwd(self) -> str:
        """Return empty so verifier falls back to persisted workspace_root.

        `oly ls --json` only exposes the session startup cwd, which is not
        reliable after runtime `cd` operations. Returning the stale startup cwd
        is worse than using the run's known workspace root fallback.
        """
        return ""

    def session_id(self) -> str:
        return self._session_id

    def doctor(self) -> dict:
        """Check oly daemon and session health."""
        issues: list[str] = []

        if not shutil.which("oly"):
            issues.append("oly CLI not found in PATH")
            return {"ok": False, "issues": issues}

        # Check daemon
        try:
            self._oly("ls")
        except OpenRelaySurfaceError as e:
            issues.append(f"oly daemon not reachable: {e}")
            return {"ok": False, "issues": issues}

        # Check session exists
        try:
            result = self._oly("ls", "--json")
            sessions = json.loads(result.stdout)
            found = any(
                str(s.get("id", "")) == self._session_id
                for s in (sessions if isinstance(sessions, list) else [])
            )
            if not found:
                issues.append(f"session {self._session_id} not found")
        except (json.JSONDecodeError, OpenRelaySurfaceError) as e:
            issues.append(f"cannot list sessions: {e}")

        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "session_id": self._session_id,
        }

    @staticmethod
    def _oly(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["oly", *args],
                capture_output=True, text=True, timeout=15,
            )
        except FileNotFoundError:
            raise OpenRelaySurfaceError("oly CLI not found")
        except subprocess.TimeoutExpired:
            raise OpenRelaySurfaceError(f"oly {' '.join(args)} timed out")
        if result.returncode != 0:
            raise OpenRelaySurfaceError(
                f"oly {' '.join(args)} failed (rc={result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result

    def _strip_pending_echoes(self, content: str) -> str:
        if not self._pending_echo_filters:
            return content
        lines = content.splitlines(keepends=True)
        remaining: list[str] = []
        for echo in self._pending_echo_filters:
            matched = False
            if echo:
                for idx in range(len(lines) - 1, -1, -1):
                    if lines[idx].rstrip("\r\n") == echo:
                        del lines[idx]
                        matched = True
                        break
            if not matched:
                remaining.append(echo)
        self._pending_echo_filters = remaining
        return "".join(lines).lstrip("\n")
