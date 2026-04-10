"""Factory for creating ExecutionSurface instances from config."""
from __future__ import annotations


def create_surface(surface_type: str, target: str, **kwargs):
    """Create a SessionAdapter implementation based on surface_type.

    Parameters
    ----------
    surface_type : str
        "tmux" or "open_relay"
    target : str
        Surface-specific target identifier:
        - tmux: pane label or %id (e.g., "my-pane" or "%0")
        - open_relay: oly session id
    """
    if surface_type == "tmux":
        from supervisor.terminal.adapter import TerminalAdapter
        return TerminalAdapter(target, **kwargs)
    elif surface_type == "open_relay":
        from supervisor.adapters.open_relay_surface import OpenRelaySurface
        return OpenRelaySurface(target)
    else:
        raise ValueError(f"unknown surface type: {surface_type!r} (expected 'tmux' or 'open_relay')")
