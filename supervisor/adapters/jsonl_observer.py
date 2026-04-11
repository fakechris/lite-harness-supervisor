"""JSONL transcript observer — reads agent JSONL files instead of terminal.

This is the terminal-free observation mode. Instead of capturing tmux pane
output, it tails the agent's native JSONL transcript files and extracts
checkpoint events from them.

Supports:
- Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
- Claude Code: ~/.claude/transcripts/ses_*.jsonl
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class JsonlObserverError(RuntimeError):
    pass


class JsonlObserver:
    """SessionAdapter implementation that reads JSONL transcript files.

    Instead of tmux capture-pane, tails a JSONL file for new events.
    Checkpoints are found by searching for <checkpoint> blocks in
    tool_result / response text within the JSONL events.
    """

    def __init__(self, jsonl_path: str, *, cwd: str = "", session_id_override: str = ""):
        if not jsonl_path:
            raise JsonlObserverError("jsonl_path must not be empty")
        self._path = Path(jsonl_path)
        self._cwd = cwd
        self._session_id_override = session_id_override
        self._offset = 0  # bytes read so far
        self._detected_cwd: str | None = None

    def read(self, lines: int = 100) -> str:
        """Read new content from JSONL file since last read.

        Returns concatenated text from recent events (tool outputs,
        messages, etc.) — similar to what terminal capture would show.
        """
        if not self._path.exists():
            return ""

        try:
            with self._path.open("r", encoding="utf-8") as f:
                f.seek(self._offset)
                new_content = f.read()
                self._offset = f.tell()
        except OSError:
            return ""

        if not new_content.strip():
            return ""

        # Extract text content from JSONL events
        text_parts: list[str] = []
        for line in new_content.strip().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Extract cwd from session metadata
            etype = event.get("type", "")
            if etype in ("session_meta", "turn_context"):
                cwd = event.get("payload", {}).get("cwd", "")
                if cwd:
                    self._detected_cwd = cwd

            # Extract text from various event types
            text = self._extract_text(event)
            if text:
                text_parts.append(text)

        return "\n".join(text_parts[-lines:])

    def inject(self, text: str) -> None:
        """Cannot inject via JSONL — use hook-based injection instead.

        In JSONL observation mode, injection happens via:
        1. Stop hook returning instruction as reason
        2. File-based handoff (.supervisor/runtime/next_instruction.txt)
        """
        # Write instruction to file for hook to pick up
        runtime_dir = Path(".supervisor/runtime")
        runtime_dir.mkdir(parents=True, exist_ok=True)
        instruction_file = runtime_dir / "next_instruction.txt"
        instruction_file.write_text(text, encoding="utf-8")

    def current_cwd(self) -> str:
        """Return cwd from JSONL metadata or constructor override."""
        if self._cwd:
            return self._cwd
        if self._detected_cwd:
            return self._detected_cwd
        return ""

    def session_id(self) -> str:
        """Return session ID from filename or override."""
        if self._session_id_override:
            return self._session_id_override
        name = self._path.stem
        # Codex: rollout-2025-10-27T01-57-16-{uuid}
        if name.startswith("rollout-"):
            parts = name.split("-", 7)
            if len(parts) > 7:
                return parts[7]
        # Claude: ses_{id}
        if name.startswith("ses_"):
            return name[4:]
        return name

    def doctor(self) -> dict:
        """Check if JSONL file exists and is readable."""
        issues: list[str] = []

        if not self._path.exists():
            issues.append(f"JSONL file not found: {self._path}")
        elif not self._path.is_file():
            issues.append(f"not a file: {self._path}")
        else:
            try:
                size = self._path.stat().st_size
                if size == 0:
                    issues.append("JSONL file is empty")
            except OSError as e:
                issues.append(f"cannot stat: {e}")

        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "jsonl_path": str(self._path),
            "offset": self._offset,
        }

    @staticmethod
    def _extract_text(event: dict) -> str:
        """Extract human-readable text from a JSONL event."""
        etype = event.get("type", "")
        payload = event.get("payload", {})

        # Codex: event_msg with tool results
        if etype == "event_msg":
            content = payload.get("content", "")
            if isinstance(content, str) and content:
                return content
            # Tool output
            output = payload.get("output", "")
            if isinstance(output, str) and output:
                return output

        # Codex: response_item (assistant messages, tool calls)
        if etype == "response_item":
            text_parts = []
            content = payload.get("content", [])
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        t = part.get("text", "") or part.get("output", "")
                        if t:
                            text_parts.append(t)
            if text_parts:
                return " ".join(text_parts)

        # Claude: tool_result
        if etype == "tool_result":
            result = payload.get("content", "")
            if isinstance(result, str):
                return result

        # Claude: tool_use (show what was executed)
        if etype == "tool_use":
            tool = payload.get("tool_name", "")
            inp = payload.get("tool_input", {})
            if tool == "bash":
                cmd = inp.get("command", "")
                return f"$ {cmd}" if cmd else ""

        return ""
