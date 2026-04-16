"""Runtime configuration with file / env / defaults layering."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml


# Global config directory (overridable for testing)
_GLOBAL_CONFIG_ENV = "THIN_SUPERVISOR_GLOBAL_CONFIG"

# Fields safe to inherit from global config into any project
_GLOBAL_INHERITABLE = frozenset({
    "worker_provider", "worker_model", "judge_model",
    "judge_temperature", "judge_max_tokens", "worker_trust_level",
    "notification_channels", "pause_handling_mode", "max_auto_interventions",
    "poll_interval_sec", "read_lines",
    "explainer_model", "explainer_temperature", "explainer_max_tokens",
})


def global_config_path() -> Path:
    """Return the global defaults config path."""
    env = os.environ.get(_GLOBAL_CONFIG_ENV, "").strip()
    if env:
        return Path(env)
    return Path.home() / ".config" / "thin-supervisor" / "defaults.yaml"


def _update_config_file(path: Path, key: str, value) -> Path:
    """Read-modify-write a single key in a YAML config file with file locking."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / f".{path.name}.lock"
    lock_fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        data = {}
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data[key] = value
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False)
            os.replace(tmp_path, str(path))
        except Exception:
            os.unlink(tmp_path)
            raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    return path


def save_global_config(key: str, value) -> Path:
    """Write a single key to the global config, creating it if needed."""
    return _update_config_file(global_config_path(), key, value)


def save_project_config(key: str, value, project_dir: str | Path = ".") -> Path:
    """Write a single key to the project config."""
    path = Path(project_dir) / ".supervisor" / "config.yaml"
    return _update_config_file(path, key, value)


def coerce_config_value(key: str, value: str):
    """Coerce a string value to the correct type for a RuntimeConfig field."""
    known = {f.name: f for f in fields(RuntimeConfig)}
    if key not in known:
        return value
    ftype = known[key].type
    if value.lower() in ("null", "none", "~"):
        return None
    if ftype in ("float", float):
        return float(value)
    if ftype in ("int", int):
        return int(value)
    return value


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

    # -- LLM Explainer (operator-facing, separate from judge) --
    explainer_model: str | None = None  # None = stub mode (cheap/fast default)
    explainer_temperature: float = 0.3
    explainer_max_tokens: int = 1024

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
        """Load with priority: defaults → global (inheritable) → project → env.

        Global config applies only inheritable fields. Project config applies
        all fields. Environment variables override everything.
        """
        base = cls()
        known = {f.name: f for f in fields(cls)}

        # 1. Global config — inheritable fields only
        gpath = global_config_path()
        if gpath.exists():
            try:
                gdata = yaml.safe_load(gpath.read_text(encoding="utf-8")) or {}
                for k, v in gdata.items():
                    if k in known and k in _GLOBAL_INHERITABLE:
                        setattr(base, k, v)
            except Exception:
                pass  # corrupt global config — skip silently

        # 2. Project config — all fields
        if config_path and Path(config_path).exists():
            pdata = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
            for k, v in pdata.items():
                if k in known:
                    setattr(base, k, v)

        # 3. Env vars override everything
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
