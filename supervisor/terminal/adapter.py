"""Terminal adapter wrapping tmux commands for pane read/write.

Inspired by smux's tmux-bridge: read-before-act guard, label-based
pane addressing, and socket auto-detection.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class PaneInfo:
    pane_id: str
    session_window: str
    size: str
    process: str
    label: str
    cwd: str


class TerminalAdapterError(RuntimeError):
    pass


class ReadGuardError(TerminalAdapterError):
    pass


class InjectionConfirmationError(TerminalAdapterError):
    pass


class TerminalAdapter:
    """Python wrapper around tmux for pane observation and injection.

    Implements the read-before-act guard: you must call ``read()`` before
    ``type_text()`` or ``send_keys()`` on a given pane.  After any write
    operation the guard resets, requiring another ``read()`` first.
    """

    def __init__(self, pane_target: str, *, tmux_socket: str | None = None):
        self._raw_target = pane_target
        self._socket = tmux_socket or self._detect_socket()
        self._pane_id: str | None = None  # resolved on first use
        self._read_guard: set[str] = set()  # pane ids that have been read

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, lines: int = 100) -> str:
        """Capture the last *lines* lines from the pane."""
        target = self._resolve_target()
        result = self._tmux(
            "capture-pane", "-t", target, "-p", "-J", "-S", f"-{lines}"
        )
        self._read_guard.add(target)
        return result.stdout

    def type_text(self, text: str) -> None:
        """Send literal text to the pane (no trailing Enter)."""
        target = self._resolve_target()
        self._require_read(target)
        self._tmux("send-keys", "-t", target, "-l", "--", text)
        self._read_guard.discard(target)

    def send_keys(self, *keys: str) -> None:
        """Send one or more special keys (Enter, C-c, Escape, …)."""
        target = self._resolve_target()
        self._require_read(target)
        for key in keys:
            self._tmux("send-keys", "-t", target, key)
        self._read_guard.discard(target)

    def inject(self, text: str) -> None:
        """Type text and press Enter in one guarded operation.

        This avoids the ReadGuardError that would occur if ``type_text()``
        and ``send_keys("Enter")`` were called separately, since
        ``type_text()`` clears the read guard.
        """
        target = self._resolve_target()
        self._require_read(target)
        self._tmux("send-keys", "-t", target, "-l", "--", text)
        self._tmux("send-keys", "-t", target, "Enter")
        self._read_guard.discard(target)
        self._confirm_injection(target, text)

    def current_cwd(self) -> str:
        """Return the current working directory of the target pane."""
        target = self._resolve_target()
        result = self._tmux(
            "display-message", "-t", target, "-p", "#{pane_current_path}"
        )
        return result.stdout.strip()

    def session_id(self) -> str:
        """Return a stable identifier for this session (pane id)."""
        return self._resolve_target()

    def list_panes(self) -> list[PaneInfo]:
        """Return metadata for every pane visible to the tmux server."""
        fmt = "#{pane_id}\t#{session_name}:#{window_index}\t#{pane_width}x#{pane_height}\t#{pane_current_command}\t#{@name}\t#{pane_current_path}"
        result = self._tmux("list-panes", "-a", "-F", fmt)
        panes: list[PaneInfo] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            panes.append(PaneInfo(
                pane_id=parts[0],
                session_window=parts[1],
                size=parts[2],
                process=parts[3],
                label=parts[4] if parts[4] else "",
                cwd=parts[5],
            ))
        return panes

    def name_pane(self, label: str, target: str | None = None) -> None:
        """Assign a human-readable label to a pane."""
        t = target or self._resolve_target()
        self._tmux("set-option", "-p", "-t", t, "@name", label)

    def resolve_label(self, label: str) -> str:
        """Look up a pane id by its ``@name`` label."""
        result = self._tmux("list-panes", "-a", "-F", "#{pane_id} #{@name}")
        for line in result.stdout.strip().splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1] == label:
                return parts[0]
        raise TerminalAdapterError(f"no pane found with label '{label}'")

    def doctor(self) -> dict:
        """Diagnostic check — returns dict with socket, pane count, issues."""
        issues: list[str] = []
        socket = self._socket or "(default)"
        pane_count = 0
        try:
            panes = self.list_panes()
            pane_count = len(panes)
        except TerminalAdapterError as exc:
            issues.append(f"cannot list panes: {exc}")
        if not shutil.which("tmux"):
            issues.append("tmux not found in PATH")
        return {
            "socket": socket,
            "pane_count": pane_count,
            "issues": issues,
            "ok": len(issues) == 0,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_target(self) -> str:
        """Resolve the user-supplied target to a concrete pane reference."""
        if self._pane_id is not None:
            return self._pane_id

        target = self._raw_target

        # Already a pane id (%N) or a session:window.pane reference
        if re.match(r"^%\d+$", target) or ":" in target or "." in target:
            self._pane_id = target
            return target

        # Pure numeric → window index, pass through
        if target.isdigit():
            self._pane_id = target
            return target

        # Otherwise treat as label
        resolved = self.resolve_label(target)
        self._pane_id = resolved
        return resolved

    def _require_read(self, target: str) -> None:
        if target not in self._read_guard:
            raise ReadGuardError(
                f"must read pane '{target}' before interacting. "
                f"Call adapter.read() first."
            )

    def _confirm_injection(self, target: str, text: str) -> None:
        needle = " ".join(text.split())
        if not needle:
            return

        for _ in range(5):
            snapshot = self._capture_tail(target, lines=30)
            if not self._tail_looks_stuck(snapshot, needle):
                return
            time.sleep(0.2)

        raise InjectionConfirmationError(
            f"submit not confirmed for pane '{target}'; injected text still visible near the tail"
        )

    def _capture_tail(self, target: str, *, lines: int) -> str:
        result = self._tmux(
            "capture-pane", "-t", target, "-p", "-J", "-S", f"-{lines}"
        )
        return result.stdout

    @staticmethod
    def _tail_looks_stuck(snapshot: str, needle: str) -> bool:
        tail = [line.strip() for line in snapshot.splitlines() if line.strip()][-3:]
        normalized_tail = [" ".join(line.split()) for line in tail]
        return any(needle in line for line in normalized_tail)

    def _detect_socket(self) -> str | None:
        """Auto-detect the tmux socket (4-level priority like smux)."""
        # Level 1: explicit env override
        explicit = os.environ.get("TMUX_BRIDGE_SOCKET")
        if explicit and os.path.exists(explicit):
            return explicit

        # Level 2: extract from $TMUX
        tmux_env = os.environ.get("TMUX", "")
        if tmux_env:
            socket = tmux_env.split(",")[0]
            if os.path.exists(socket):
                return socket

        # Level 3: scan /tmp/tmux-<uid>/
        uid = os.getuid()
        for base in (f"/tmp/tmux-{uid}", f"/private/tmp/tmux-{uid}"):
            if os.path.isdir(base):
                for entry in os.listdir(base):
                    sock = os.path.join(base, entry)
                    if self._socket_alive(sock):
                        return sock

        # Level 4: default server
        return None

    def _socket_alive(self, sock: str) -> bool:
        try:
            result = subprocess.run(
                ["tmux", "-S", sock, "list-sessions"],
                capture_output=True, timeout=3,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def _tmux(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd: list[str] = ["tmux"]
        if self._socket:
            cmd += ["-S", self._socket]
        cmd += list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            raise TerminalAdapterError("tmux not found in PATH")
        except subprocess.TimeoutExpired:
            raise TerminalAdapterError(f"tmux command timed out: {' '.join(args)}")
        if result.returncode != 0:
            raise TerminalAdapterError(
                f"tmux {' '.join(args)} failed (rc={result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result
