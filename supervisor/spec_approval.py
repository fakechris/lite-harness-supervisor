from __future__ import annotations

import shlex
from datetime import datetime, timezone
from pathlib import Path

import yaml

from supervisor.plan.loader import load_spec


class SpecApprovalRequired(ValueError):
    pass


def approval_required_message(spec_path: str) -> str:
    quoted = shlex.quote(spec_path)
    return (
        f"spec requires user approval before execution: {spec_path}. "
        f"Run: thin-supervisor spec approve --spec {quoted} --by human"
    )


def ensure_spec_is_runnable(spec, spec_path: str) -> None:
    if spec.approval.required and spec.approval.status != "approved":
        raise SpecApprovalRequired(approval_required_message(spec_path))


def load_runnable_spec(path: str):
    spec = load_spec(path)
    ensure_spec_is_runnable(spec, path)
    return spec


def approve_spec(path: str, *, approved_by: str = "human") -> dict:
    spec_path = Path(path)
    data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("spec must be a YAML mapping")
    approval = data.get("approval")
    if approval is None:
        approval = {}
    if not isinstance(approval, dict):
        raise ValueError("approval must be a YAML mapping")
    approval["required"] = approval.get("required", True)
    approval["status"] = "approved"
    approval["approved_by"] = approved_by
    approval["approved_at"] = datetime.now(timezone.utc).isoformat()
    data["approval"] = approval
    spec_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return approval
