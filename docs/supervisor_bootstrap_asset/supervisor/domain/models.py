from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

from .enums import TopState

@dataclass
class VerifyCheck:
    type: str
    payload: dict[str, Any]

@dataclass
class StepSpec:
    id: str
    type: str
    objective: str
    depends_on: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    verify: list[VerifyCheck] = field(default_factory=list)
    next: str | None = None
    options: list[dict[str, Any]] = field(default_factory=list)

@dataclass
class FinishPolicy:
    require_all_steps_done: bool = True
    require_verification_pass: bool = True
    require_clean_or_committed_repo: bool = False

@dataclass
class RuntimePolicy:
    default_continue: bool = True
    max_retries_per_node: int = 3
    max_retries_global: int = 12

@dataclass
class WorkflowSpec:
    kind: str
    id: str
    goal: str
    steps: list[StepSpec] = field(default_factory=list)
    nodes: list[StepSpec] = field(default_factory=list)
    finish_policy: FinishPolicy = field(default_factory=FinishPolicy)
    policy: RuntimePolicy = field(default_factory=RuntimePolicy)

    def ordered_nodes(self) -> list[StepSpec]:
        return self.steps if self.kind == "linear_plan" else self.nodes

    def get_node(self, node_id: str) -> StepSpec:
        for node in self.ordered_nodes():
            if node.id == node_id:
                return node
        raise KeyError(f"Unknown node_id: {node_id}")

    def first_node_id(self) -> str:
        nodes = self.ordered_nodes()
        if not nodes:
            raise ValueError("Spec has no nodes")
        return nodes[0].id

    def next_node_id(self, current_node_id: str) -> str | None:
        nodes = self.ordered_nodes()
        if self.kind == "linear_plan":
            ids = [x.id for x in nodes]
            idx = ids.index(current_node_id)
            return ids[idx + 1] if idx + 1 < len(ids) else None
        node = self.get_node(current_node_id)
        return node.next

@dataclass
class RetryBudget:
    per_node: int = 3
    global_limit: int = 12
    used_global: int = 0

@dataclass
class SupervisorState:
    run_id: str
    spec_id: str
    mode: str
    top_state: TopState
    current_node_id: str
    current_attempt: int = 0
    done_node_ids: list[str] = field(default_factory=list)
    branch_history: list[dict[str, Any]] = field(default_factory=list)
    human_escalations: list[dict[str, Any]] = field(default_factory=list)
    retry_budget: RetryBudget = field(default_factory=RetryBudget)
    last_agent_checkpoint: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=lambda: {"last_status": "pending"})
    last_event: dict[str, Any] = field(default_factory=dict)
    last_decision: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupervisorState":
        rb = data.get("retry_budget", {})
        return cls(
            run_id=data["run_id"],
            spec_id=data["spec_id"],
            mode=data["mode"],
            top_state=TopState(data["top_state"]),
            current_node_id=data["current_node_id"],
            current_attempt=data.get("current_attempt", 0),
            done_node_ids=data.get("done_node_ids", []),
            branch_history=data.get("branch_history", []),
            human_escalations=data.get("human_escalations", []),
            retry_budget=RetryBudget(
                per_node=rb.get("per_node", 3),
                global_limit=rb.get("global_limit", 12),
                used_global=rb.get("used_global", 0),
            ),
            last_agent_checkpoint=data.get("last_agent_checkpoint", {}),
            verification=data.get("verification", {"last_status": "pending"}),
            last_event=data.get("last_event", {}),
            last_decision=data.get("last_decision", {}),
        )
