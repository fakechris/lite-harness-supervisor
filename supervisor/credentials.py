"""Credential resolution and persistence for runtime config."""
from __future__ import annotations

from dataclasses import dataclass

from supervisor.config import RuntimeConfig, save_global_config, save_project_config


@dataclass
class MissingCredential:
    key: str
    description: str
    required: bool
    scope: str  # "global" | "project"


# Known credentials that may need user input.
# required=False means the system can run in degraded mode without them.
CREDENTIAL_SPECS = [
    {
        "key": "worker_provider",
        "description": "Worker provider (anthropic, openai, minimax)",
        "default_value": "unknown",
        "required": False,
        "scope": "global",
    },
    {
        "key": "worker_model",
        "description": "Worker model name (e.g. claude-opus-4-6)",
        "default_value": "",
        "required": False,
        "scope": "global",
    },
    {
        "key": "judge_model",
        "description": "LLM judge model (e.g. anthropic/claude-haiku-4-5-20251001, or null for stub mode)",
        "default_value": None,
        "required": False,
        "scope": "global",
    },
]


def resolve_credentials(config: RuntimeConfig) -> list[MissingCredential]:
    """Return credentials that are missing or still at defaults."""
    missing = []
    for spec in CREDENTIAL_SPECS:
        current = getattr(config, spec["key"], None)
        if current == spec["default_value"]:
            missing.append(MissingCredential(
                key=spec["key"],
                description=spec["description"],
                required=spec["required"],
                scope=spec["scope"],
            ))
    return missing


def persist_credential(
    key: str,
    value,
    scope: str = "global",
    project_dir: str = ".",
) -> None:
    """Write a credential to the appropriate config file."""
    if scope == "global":
        save_global_config(key, value)
    else:
        save_project_config(key, value, project_dir=project_dir)
