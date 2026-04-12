"""Runtime configuration with file / env / defaults layering."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml


@dataclass
class RuntimeConfig:
    # -- Execution Surface --
    surface_type: str = "tmux"    # "tmux" | "open_relay"
    surface_target: str = ""     # pane label/%id (tmux) or session id (open_relay)
    pane_target: str = ""        # legacy alias for surface_target (tmux compat)
    poll_interval_sec: float = 2.0
    read_lines: int = 100

    # -- Worker Profile --
    worker_provider: str = "unknown"    # anthropic | openai | minimax | ...
    worker_model: str = ""              # claude-opus-4-6 | gpt-5.4 | ...
    worker_trust_level: str = "standard"  # low | standard | high

    # -- LLM Judge --
    judge_model: str | None = None  # None = stub mode
    judge_temperature: float = 0.1
    judge_max_tokens: int = 512

    # -- Runtime paths --
    runtime_dir: str = ".supervisor/runtime"
    state_file: str = ".supervisor/runtime/state.json"
    event_log_file: str = ".supervisor/runtime/event_log.jsonl"
    decision_log_file: str = ".supervisor/runtime/decision_log.jsonl"

    # -- Retry (overridable, spec values take precedence) --
    max_retries_per_node: int = 3
    max_retries_global: int = 12

    # -- Gate --
    branch_confidence_threshold: float = 0.75
    default_agent_timeout_sec: int = 300

    # -- Notifications --
    notification_channels: list[dict] = field(default_factory=lambda: [
        {"kind": "tmux_display"},
        {"kind": "jsonl"},
    ])
    pause_handling_mode: str = "notify_then_ai"  # notify_only | notify_then_ai
    max_auto_interventions: int = 2

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str | Path) -> "RuntimeConfig":
        """Load config from a YAML file, ignoring unknown keys."""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    def from_env(cls, prefix: str = "SUPERVISOR_") -> "RuntimeConfig":
        """Build config from ``SUPERVISOR_*`` environment variables."""
        import os

        data: dict = {}
        known = {f.name: f for f in fields(cls)}
        for key, val in os.environ.items():
            if not key.startswith(prefix):
                continue
            field_name = key[len(prefix):].lower()
            if field_name not in known:
                continue
            ftype = known[field_name].type
            if ftype in ("float", float):
                data[field_name] = float(val)
            elif ftype in ("int", int):
                data[field_name] = int(val)
            else:
                data[field_name] = val
        return cls(**data)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "RuntimeConfig":
        """Load with priority: *config_path* → env → defaults.

        Values from the file are applied first, then env vars override.
        """
        base = cls()
        if config_path and Path(config_path).exists():
            base = cls.from_file(config_path)
        # Overlay env vars
        import os
        known = {f.name: f for f in fields(cls)}
        prefix = "SUPERVISOR_"
        for key, val in os.environ.items():
            if not key.startswith(prefix):
                continue
            field_name = key[len(prefix):].lower()
            if field_name not in known:
                continue
            ftype = known[field_name].type
            if ftype in ("float", float):
                setattr(base, field_name, float(val))
            elif ftype in ("int", int):
                setattr(base, field_name, int(val))
            else:
                setattr(base, field_name, val)
        return base

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def effective_target(self) -> str:
        """Resolve the effective surface target (surface_target > pane_target)."""
        return self.surface_target or self.pane_target

    def default_config_yaml(self) -> str:
        """Render a commented YAML template suitable for ``init``."""
        return (
            "# thin-supervisor config\n"
            "\n"
            "# Execution surface: tmux | open_relay\n"
            f"surface_type: \"{self.surface_type}\"\n"
            "# Surface target: pane label/%id (tmux) or session id (open_relay)\n"
            f"surface_target: \"\"\n"
            f"poll_interval_sec: {self.poll_interval_sec}\n"
            f"read_lines: {self.read_lines}\n"
            "\n"
            "# Worker profile (affects supervision intensity)\n"
            "# provider: anthropic | openai | minimax | ...\n"
            f"worker_provider: \"{self.worker_provider}\"\n"
            "# model: claude-opus-4-6 | gpt-5.4 | ...\n"
            f"worker_model: \"{self.worker_model}\"\n"
            "# trust: low | standard | high (high = minimal supervision)\n"
            f"worker_trust_level: \"{self.worker_trust_level}\"\n"
            "\n"
            "# LLM judge (set to null for stub/offline mode)\n"
            "# Examples: anthropic/claude-haiku-4-5-20251001, openai/gpt-4o-mini\n"
            f"judge_model: null\n"
            f"judge_temperature: {self.judge_temperature}\n"
            f"judge_max_tokens: {self.judge_max_tokens}\n"
            "\n"
            "# Runtime\n"
            f"runtime_dir: \"{self.runtime_dir}\"\n"
            "\n"
            "# Notification channels used when a run pauses for human.\n"
            "# Built-ins today: tmux_display, jsonl\n"
            "notification_channels:\n"
            "  - kind: \"tmux_display\"\n"
            "  - kind: \"jsonl\"\n"
            "\n"
            "# Pause handling strategy.\n"
            "# notify_only: pause and wait\n"
            "# notify_then_ai: notify, then let the agent try an automatic recovery first\n"
            f"pause_handling_mode: \"{self.pause_handling_mode}\"\n"
            f"max_auto_interventions: {self.max_auto_interventions}\n"
        )
