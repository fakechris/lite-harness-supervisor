"""Install/uninstall the supervisor Stop hook in Claude Code / Codex settings.

Both agents use the same JSON settings shape:

    {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "..."}]}]}}

Claude Code reads ``~/.claude/settings.json``; Codex reads ``~/.codex/hooks.json``.
Install is idempotent and safe to merge alongside other hooks (existing user
entries are preserved; only our own command entry is added/removed).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

STOP_EVENT = "Stop"
DEFAULT_COMMAND = "thin-supervisor hook stop"


@dataclass
class AgentSettings:
    label: str
    settings_path: Path


def claude_settings_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude" / "settings.json"


def codex_hooks_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".codex" / "hooks.json"


class SettingsError(RuntimeError):
    """Raised when an existing settings file is unreadable or malformed.

    We never silently overwrite a malformed settings file — the user likely
    has hand-edited config there and we don't want to clobber it.
    """


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        raise SettingsError(f"{path}: cannot read settings file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SettingsError(
            f"{path}: settings file is not valid JSON ({exc}); "
            "refusing to overwrite"
        ) from exc
    if not isinstance(data, dict):
        raise SettingsError(
            f"{path}: settings root is not an object; refusing to overwrite"
        )
    return data


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _hook_entry(command: str) -> dict:
    return {"hooks": [{"type": "command", "command": command}]}


def _find_our_entry(stop_list: list, command: str) -> tuple[int, int] | None:
    """Return (group_idx, hook_idx) for the entry whose command matches."""
    for gi, group in enumerate(stop_list):
        if not isinstance(group, dict):
            continue
        hooks = group.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hi, hook in enumerate(hooks):
            if isinstance(hook, dict) and hook.get("command") == command:
                return gi, hi
    return None


def install_stop_hook(
    settings_path: Path, command: str = DEFAULT_COMMAND,
) -> tuple[bool, str]:
    """Add a Stop hook that runs ``command``. Idempotent.

    Returns (changed, message).
    """
    try:
        data = _load_settings(settings_path)
    except SettingsError as exc:
        return False, str(exc)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return False, f"{settings_path}: `hooks` field is not an object; refusing to merge"

    stop_list = hooks.setdefault(STOP_EVENT, [])
    if not isinstance(stop_list, list):
        return False, f"{settings_path}: `hooks.Stop` is not a list; refusing to merge"

    if _find_our_entry(stop_list, command) is not None:
        return False, f"{settings_path}: Stop hook already installed"

    stop_list.append(_hook_entry(command))
    _atomic_write_json(settings_path, data)
    return True, f"{settings_path}: installed Stop hook -> `{command}`"


def uninstall_stop_hook(
    settings_path: Path, command: str = DEFAULT_COMMAND,
) -> tuple[bool, str]:
    """Remove our Stop hook entry. Returns (changed, message)."""
    if not settings_path.exists():
        return False, f"{settings_path}: no settings file to modify"

    try:
        data = _load_settings(settings_path)
    except SettingsError as exc:
        return False, str(exc)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False, f"{settings_path}: no `hooks` section"

    stop_list = hooks.get(STOP_EVENT)
    if not isinstance(stop_list, list):
        return False, f"{settings_path}: no Stop hook configured"

    found = _find_our_entry(stop_list, command)
    if found is None:
        return False, f"{settings_path}: Stop hook not present"

    gi, hi = found
    # Remove the matching hook; remove the enclosing group if it becomes empty;
    # remove the Stop list if it becomes empty; remove the hooks dict if empty.
    del stop_list[gi]["hooks"][hi]
    if not stop_list[gi]["hooks"]:
        del stop_list[gi]
    if not stop_list:
        del hooks[STOP_EVENT]
    if not hooks:
        del data["hooks"]

    _atomic_write_json(settings_path, data)
    return True, f"{settings_path}: removed Stop hook"


def resolve_targets(agent: str, home: Path | None = None) -> list[AgentSettings]:
    """Return settings targets for ``agent`` ∈ {claude, codex, both}.

    Only returns a target if the agent's home directory exists — we never
    create ``~/.claude`` or ``~/.codex`` on the user's behalf.
    """
    base = home or Path.home()
    out: list[AgentSettings] = []
    if agent in ("claude", "both") and (base / ".claude").exists():
        out.append(AgentSettings("Claude Code", claude_settings_path(home=base)))
    if agent in ("codex", "both") and (base / ".codex").exists():
        out.append(AgentSettings("Codex", codex_hooks_path(home=base)))
    return out
