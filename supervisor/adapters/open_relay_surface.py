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
        self._session_id = session_id

    def read(self, lines: int = 100) -> str:
        """Read recent output from the oly session."""
        result = self._oly("logs", self._session_id,
                           "--tail", str(lines), "--no-truncate")
        return result.stdout

    def inject(self, text: str) -> None:
        """Send text + Enter to the oly session."""
        self._oly("send", self._session_id, text, "key:enter")

    def current_cwd(self) -> str:
        """Get the session's working directory from metadata.

        open-relay tracks the cwd the session was started with.
        This may not reflect runtime `cd` operations by the agent.
        Returns empty string if unavailable (verifier will fall back).
        """
        try:
            result = self._oly("ls", "--json")
            sessions = json.loads(result.stdout)
            if isinstance(sessions, list):
                for s in sessions:
                    if str(s.get("id", "")).startswith(self._session_id):
                        return s.get("cwd", "")
        except (json.JSONDecodeError, OpenRelaySurfaceError):
            pass
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
                str(s.get("id", "")).startswith(self._session_id)
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
