"""SessionAdapter protocol — unified ExecutionSurface interface.

The supervisor loop depends ONLY on this protocol. Concrete implementations:
- TerminalAdapter (tmux panes)
- OpenRelaySurface (oly sessions)
- Future: PTY wrappers, SSH tunnels, etc.
"""
from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionAdapter(Protocol):
    """Unified interface between supervisor (brain) and execution environment (hands)."""

    def read(self, lines: int = 100) -> str:
        """Capture recent output from the agent session."""
        ...

    def inject(self, text: str) -> None:
        """Send instruction text to the agent (includes submission)."""
        ...

    def current_cwd(self) -> str:
        """Return the agent's current working directory.

        May return empty string if the surface cannot determine cwd.
        Verifiers should fall back to supervisor's cwd in that case.
        """
        ...

    def session_id(self) -> str:
        """Return a stable identifier for this session."""
        ...

    def doctor(self) -> dict:
        """Check connectivity and health.

        Returns dict with at least:
        - ok: bool — overall health
        - issues: list[str] — any problems found
        """
        ...
