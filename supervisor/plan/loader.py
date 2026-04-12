from __future__ import annotations
import pathlib
import yaml

from supervisor.domain.models import WorkflowSpec, StepSpec, VerifyCheck, BranchOption, FinishPolicy, RuntimePolicy, AcceptanceContract, SpecApproval

class SpecValidationError(ValueError):
    pass

def _parse_verify(items):
    out = []
    for item in items or []:
        if "type" not in item:
            raise SpecValidationError("verify item missing `type`")
        payload = {k: v for k, v in item.items() if k != "type"}
        out.append(VerifyCheck(type=item["type"], payload=payload))
    return out

def _parse_options(items):
    options = []
    for o in items or []:
        if not isinstance(o, dict):
            raise SpecValidationError("branch option must be a mapping")
        for key in ["id", "next"]:
            if key not in o:
                raise SpecValidationError(f"branch option missing `{key}`")
        options.append(BranchOption(
            id=o["id"], next=o["next"],
            label=o.get("label"), when_hint=o.get("when_hint"),
        ))
    return options

def _parse_nodes(items):
    nodes = []
    for item in items or []:
        for key in ["id", "type", "objective"]:
            if key not in item:
                raise SpecValidationError(f"node missing `{key}`")
        nodes.append(
            StepSpec(
                id=item["id"],
                type=item["type"],
                objective=item["objective"],
                depends_on=item.get("depends_on", []),
                outputs=item.get("outputs", []),
                verify=_parse_verify(item.get("verify", [])),
                next=item.get("next"),
                options=_parse_options(item.get("options", [])),
            )
        )
    return nodes

def load_spec(path: str) -> WorkflowSpec:
    data = yaml.safe_load(pathlib.Path(path).read_text())
    if not isinstance(data, dict):
        raise SpecValidationError("spec must be a YAML mapping")
    for key in ["kind", "id", "goal"]:
        if key not in data:
            raise SpecValidationError(f"spec missing `{key}`")

    kind = data["kind"]
    if kind not in {"linear_plan", "conditional_workflow"}:
        raise SpecValidationError(f"unsupported kind: {kind}")

    finish_policy = FinishPolicy(**data.get("finish_policy", {}))
    policy = RuntimePolicy(**data.get("policy", {}))

    # Parse acceptance contract (optional, backward compat with finish_policy)
    acceptance = None
    if "acceptance" in data:
        acc = data["acceptance"]
        if not isinstance(acc, dict):
            raise SpecValidationError("acceptance must be a YAML mapping")
        acceptance = AcceptanceContract(
            goal=acc.get("goal", data.get("goal", "")),
            required_evidence=acc.get("required_evidence", []),
            forbidden_states=acc.get("forbidden_states", []),
            risk_class=acc.get("risk_class", "standard"),
            must_review_by=acc.get("must_review_by", ""),
            require_all_steps_done=acc.get("require_all_steps_done", finish_policy.require_all_steps_done),
            require_verification_pass=acc.get("require_verification_pass", finish_policy.require_verification_pass),
            require_clean_or_committed_repo=acc.get("require_clean_or_committed_repo", finish_policy.require_clean_or_committed_repo),
        )
    else:
        acceptance = AcceptanceContract.from_finish_policy(finish_policy, goal=data.get("goal", ""))

    approval_data = data.get("approval", {})
    if approval_data is None:
        approval_data = {}
    if not isinstance(approval_data, dict):
        raise SpecValidationError("approval must be a YAML mapping")
    approval_required = approval_data.get("required", False)
    approval = SpecApproval(
        required=approval_required,
        status=approval_data.get("status", "draft" if approval_required else "approved"),
        approved_by=approval_data.get("approved_by", ""),
        approved_at=approval_data.get("approved_at", ""),
    )

    steps = _parse_nodes(data.get("steps", []))
    nodes = _parse_nodes(data.get("nodes", []))

    if kind == "linear_plan" and not steps:
        raise SpecValidationError("linear_plan requires `steps`")
    if kind == "conditional_workflow" and not nodes:
        raise SpecValidationError("conditional_workflow requires `nodes`")

    return WorkflowSpec(
        kind=kind,
        id=data["id"],
        goal=data["goal"],
        steps=steps,
        nodes=nodes,
        finish_policy=finish_policy,
        policy=policy,
        acceptance=acceptance,
        approval=approval,
    )
