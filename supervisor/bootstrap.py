"""Zero-setup bootstrap: auto-detect, init, start daemon, validate surface."""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path

from supervisor.config import RuntimeConfig

logger = logging.getLogger(__name__)

CONFIG_FILE = ".supervisor/config.yaml"
SUPERVISOR_DIR = ".supervisor"


@dataclass
class BootstrapResult:
    ok: bool = True
    steps: list[dict] = field(default_factory=list)
    config: RuntimeConfig | None = None
    pane_target: str = ""
    surface_type: str = ""
    missing_credentials: list[dict] = field(default_factory=list)
    error: str = ""


def _step(name: str, status: str, message: str) -> dict:
    return {"name": name, "status": status, "message": message}


def bootstrap(cwd: str | None = None) -> BootstrapResult:
    """Full bootstrap: tmux check → init/repair → config → daemon → pane detect.

    Returns a BootstrapResult with step-by-step outcomes. On failure,
    result.ok is False and result.error describes what went wrong.
    """
    result = BootstrapResult()
    original_cwd = os.getcwd()
    if cwd:
        os.chdir(cwd)

    try:
        _do_bootstrap(result)
    finally:
        if cwd:
            os.chdir(original_cwd)

    return result


def _do_bootstrap(result: BootstrapResult) -> None:
    # Step 1: tmux check
    tmux_env = os.environ.get("TMUX", "")
    if not tmux_env:
        result.steps.append(_step("tmux_check", "failed", "not inside a tmux session"))
        result.ok = False
        result.error = "must run inside a tmux session ($TMUX not set)"
        return
    result.steps.append(_step("tmux_check", "ok", "inside tmux"))

    # Step 2: init/repair
    supervisor_dir = Path(SUPERVISOR_DIR)
    if supervisor_dir.exists():
        result.steps.append(_step("init_repair", "skipped", ".supervisor/ already exists"))
    else:
        try:
            _auto_init()
            result.steps.append(_step("init_repair", "ok", "created .supervisor/"))
        except Exception as exc:
            result.steps.append(_step("init_repair", "failed", str(exc)))
            result.ok = False
            result.error = f"auto-init failed: {exc}"
            return

    # Step 3: config load
    try:
        config = RuntimeConfig.load(CONFIG_FILE)
        result.config = config
        result.steps.append(_step("config_load", "ok", "config loaded"))
    except Exception as exc:
        result.steps.append(_step("config_load", "failed", str(exc)))
        result.ok = False
        result.error = f"config load failed: {exc}"
        return

    # Step 4: daemon ensure
    try:
        _ensure_daemon_running(config)
        result.steps.append(_step("daemon_ensure", "ok", "daemon running"))
    except Exception as exc:
        result.steps.append(_step("daemon_ensure", "failed", str(exc)))
        result.ok = False
        result.error = f"daemon start failed: {exc}"
        return

    # Step 5: pane detect + validate
    pane = os.environ.get("TMUX_PANE", "")
    if not pane:
        result.steps.append(_step("pane_detect", "failed", "$TMUX_PANE not set"))
        result.ok = False
        result.error = "could not detect tmux pane ($TMUX_PANE not set)"
        return
    result.pane_target = pane
    result.surface_type = config.surface_type or "tmux"

    # Validate pane is accessible and not locked by another run
    pane_issue = _validate_pane(pane)
    if pane_issue:
        result.steps.append(_step("pane_detect", "failed", pane_issue))
        result.ok = False
        result.error = pane_issue
        return
    result.steps.append(_step("pane_detect", "ok", f"pane={pane}"))

    # Step 6: credential check (optional, does not block)
    from supervisor.credentials import resolve_credentials
    missing = resolve_credentials(config)
    result.missing_credentials = [
        {"key": m.key, "description": m.description, "scope": m.scope}
        for m in missing
    ]
    if missing:
        result.steps.append(_step(
            "credentials", "ok",
            f"{len(missing)} optional credential(s) not configured",
        ))
    else:
        result.steps.append(_step("credentials", "ok", "all credentials configured"))


def _auto_init() -> None:
    """Minimal init — create scaffold without requiring argparse."""
    base = Path(SUPERVISOR_DIR)
    base.mkdir(parents=True, exist_ok=True)
    (base / "runtime").mkdir(parents=True, exist_ok=True)
    (base / "specs").mkdir(parents=True, exist_ok=True)
    (base / "clarify").mkdir(parents=True, exist_ok=True)
    (base / "plans").mkdir(parents=True, exist_ok=True)

    config_path = Path(CONFIG_FILE)
    if not config_path.exists():
        # Write minimal config — don't set inheritable fields so global config flows through
        config_path.write_text(
            "# thin-supervisor config\n"
            "# Project-local settings. Global defaults: ~/.config/thin-supervisor/defaults.yaml\n"
            "surface_type: tmux\n",
            encoding="utf-8",
        )

    # Append to .gitignore if present
    gitignore = Path(".gitignore")
    if gitignore.exists():
        content = gitignore.read_text()
        if ".supervisor/runtime" not in content:
            with gitignore.open("a") as f:
                f.write("\n.supervisor/runtime/\n")


def _validate_pane(pane: str) -> str | None:
    """Validate that the pane is accessible and not locked. Returns issue or None."""
    # Check pane is actually accessible via tmux
    try:
        import subprocess
        result = subprocess.run(
            ["tmux", "display-message", "-t", pane, "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return f"pane {pane} is not accessible: {result.stderr.strip()}"
    except FileNotFoundError:
        return "tmux command not found"
    except Exception as exc:
        return f"pane validation failed: {exc}"

    # Check pane is not locked by another run
    try:
        from supervisor.global_registry import find_pane_owner
        owner = find_pane_owner(pane)
        if owner:
            mode = owner.get("controller_mode", "unknown")
            run_id = owner.get("run_id", "?")
            spec = owner.get("spec_path", "?")
            if mode == "foreground":
                return (
                    f"pane {pane} is owned by foreground debug run {run_id} "
                    f"(spec: {spec}); stop the foreground run or use a different pane"
                )
            return (
                f"pane {pane} is owned by daemon run {run_id} (spec: {spec}); "
                f"use 'thin-supervisor observe {run_id}' to watch or "
                f"'thin-supervisor run stop {run_id}' to release"
            )
    except Exception:
        pass  # registry check is best-effort

    return None


def _ensure_daemon_running(config: RuntimeConfig | None = None) -> None:
    """Ensure daemon is running, auto-start if needed."""
    from supervisor.daemon.client import DaemonClient

    client = DaemonClient()
    if client.is_running():
        return

    import time
    from supervisor.app import _fork_daemon

    if config is None:
        config = RuntimeConfig.load(CONFIG_FILE)
    _fork_daemon(config)

    for _ in range(30):
        time.sleep(0.2)
        if client.is_running():
            return

    raise RuntimeError("daemon did not start within 6s")
