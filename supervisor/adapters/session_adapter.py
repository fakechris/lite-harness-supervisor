"""SessionAdapter protocol — brain/hands decoupling per Anthropic Managed Agents."""
from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionAdapter(Protocol):
    """Unified interface between supervisor (brain) and execution environment (hands).

    TerminalAdapter implements this for tmux panes.
    Future adapters could implement it for PTY wrappers, Unix sockets, etc.
    """

    def read(self, lines: int = 100) -> str:
        """Capture recent output from the agent session."""
        ...

    def inject(self, text: str) -> None:
        """Send instruction text to the agent (includes submission)."""
        ...

    def current_cwd(self) -> str:
        """Return the agent's current working directory."""
        ...

    def session_id(self) -> str:
        """Return a stable identifier for this session."""
        ...
