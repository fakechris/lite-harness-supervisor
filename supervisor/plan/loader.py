from __future__ import annotations
import pathlib
import yaml

from supervisor.domain.models import WorkflowSpec, StepSpec, VerifyCheck, FinishPolicy, RuntimePolicy

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
                options=item.get("options", []),
            )
        )
    return nodes

def load_spec(path: str) -> WorkflowSpec:
    data = yaml.safe_load(pathlib.Path(path).read_text())
    for key in ["kind", "id", "goal"]:
        if key not in data:
            raise SpecValidationError(f"spec missing `{key}`")

    kind = data["kind"]
    if kind not in {"linear_plan", "conditional_workflow"}:
        raise SpecValidationError(f"unsupported kind: {kind}")

    finish_policy = FinishPolicy(**data.get("finish_policy", {}))
    policy = RuntimePolicy(**data.get("policy", {}))

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
    )
