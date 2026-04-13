from __future__ import annotations
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from .enums import TopState
from .state_machine import normalize_top_state

@dataclass
class VerifyCheck:
    type: str
    payload: dict[str, Any]

@dataclass
class BranchOption:
    id: str
    next: str
    label: str | None = None
    when_hint: str | None = None

@dataclass
class Checkpoint:
    """Structured checkpoint parsed from agent output."""
    status: str
    current_node: str
    summary: str
    run_id: str = ""
    checkpoint_seq: int = 0
    checkpoint_id: str = ""
    timestamp: str = ""
    surface_id: str = ""
    evidence: list[str] = field(default_factory=list)
    candidate_next_actions: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    question_for_supervisor: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.checkpoint_id:
            self.checkpoint_id = f"cp_{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class StepSpec:
    id: str
    type: str
    objective: str
    depends_on: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    verify: list[VerifyCheck] = field(default_factory=list)
    next: str | None = None
    options: list[BranchOption] = field(default_factory=list)

@dataclass
class FinishPolicy:
    require_all_steps_done: bool = True
    require_verification_pass: bool = True
    require_clean_or_committed_repo: bool = False

@dataclass
class AcceptanceContract:
    """Defines 'what counts as truly done' — distinct from FinishPolicy.

    FinishPolicy is the legacy subset. AcceptanceContract extends it with
    risk classification, evidence requirements, and reviewer gating.
    """
    goal: str = ""
    required_evidence: list[str] = field(default_factory=list)
    forbidden_states: list[str] = field(default_factory=list)
    risk_class: str = "standard"          # low | standard | high | critical
    must_review_by: str = ""              # "" | "human" | "stronger_reviewer"
    require_all_steps_done: bool = True
    require_verification_pass: bool = True
    require_clean_or_committed_repo: bool = False

    def __post_init__(self):
        valid_risk = {"low", "standard", "high", "critical"}
        valid_review = {"", "human", "stronger_reviewer"}
        if self.risk_class not in valid_risk:
            raise ValueError(f"invalid risk_class: {self.risk_class!r} (expected one of {valid_risk})")
        if self.must_review_by not in valid_review:
            raise ValueError(f"invalid must_review_by: {self.must_review_by!r} (expected one of {valid_review})")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_finish_policy(cls, fp: FinishPolicy, goal: str = "") -> "AcceptanceContract":
        """Backward compat: build from legacy FinishPolicy."""
        return cls(
            goal=goal,
            require_all_steps_done=fp.require_all_steps_done,
            require_verification_pass=fp.require_verification_pass,
            require_clean_or_committed_repo=fp.require_clean_or_committed_repo,
        )

@dataclass
class WorkerProfile:
    """Explicit description of the worker's capabilities and role."""
    worker_id: str = "default"
    provider: str = "unknown"             # anthropic | openai | minimax | ...
    model_name: str = ""                  # claude-opus-4-6 | gpt-5.4 | ...
    role: str = "executor"                # executor | reviewer
    trust_level: str = "standard"         # low | standard | high

    def __post_init__(self):
        valid_trust = {"low", "standard", "high"}
        valid_roles = {"executor", "reviewer"}
        if self.trust_level not in valid_trust:
            raise ValueError(f"invalid trust_level: {self.trust_level!r} (expected one of {valid_trust})")
        if self.role not in valid_roles:
            raise ValueError(f"invalid role: {self.role!r} (expected one of {valid_roles})")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class SupervisionPolicy:
    """Controls how strongly the supervisor intervenes."""
    mode: str = "strict_verifier"         # strict_verifier | collaborative_reviewer | directive_lead
    reason: str = "default"
    risk_class: str = "standard"
    failure_threshold: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class RoutingDecision:
    """Escalation/routing target when supervisor can't resolve alone."""
    routing_id: str = ""
    target_type: str = "human"            # human | reviewer | executor
    scope: str = ""                       # bounded_review | full_takeover | single_question
    reason: str = ""
    triggered_by_decision_id: str = ""
    consultation_id: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.routing_id:
            self.routing_id = f"rt_{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OracleOpinion:
    """Advisory second opinion from an external or fallback oracle."""
    provider: str
    model_name: str
    mode: str
    question: str
    files: list[str] = field(default_factory=list)
    response_text: str = ""
    source: str = "external"            # external | fallback
    consultation_id: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.consultation_id:
            self.consultation_id = f"oracle_{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class RuntimePolicy:
    default_continue: bool = True
    max_retries_per_node: int = 3
    max_retries_global: int = 12


@dataclass
class SpecApproval:
    required: bool = False
    status: str = "approved"            # draft | approved
    approved_by: str = ""
    approved_at: str = ""

    def __post_init__(self):
        valid_status = {"draft", "approved"}
        if self.status not in valid_status:
            raise ValueError(f"invalid approval.status: {self.status!r} (expected one of {valid_status})")

@dataclass
class WorkflowSpec:
    kind: str
    id: str
    goal: str
    steps: list[StepSpec] = field(default_factory=list)
    nodes: list[StepSpec] = field(default_factory=list)
    finish_policy: FinishPolicy = field(default_factory=FinishPolicy)
    policy: RuntimePolicy = field(default_factory=RuntimePolicy)
    acceptance: AcceptanceContract | None = None
    approval: SpecApproval = field(default_factory=SpecApproval)

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
            try:
                idx = ids.index(current_node_id)
                return ids[idx + 1] if idx + 1 < len(ids) else None
            except ValueError:
                return None
        node = self.get_node(current_node_id)
        return node.next

@dataclass
class RetryBudget:
    per_node: int = 3
    global_limit: int = 12
    used_global: int = 0

@dataclass
class SupervisorDecision:
    """First-class gate decision object."""
    decision_id: str
    decision: str              # DecisionType value (uppercase)
    reason: str
    confidence: float
    needs_human: bool
    timestamp: str
    gate_type: str             # "continue" | "branch" | "finish" | "checkpoint_status"
    triggered_by_seq: int = 0
    triggered_by_checkpoint_id: str = ""
    next_instruction: str | None = None
    selected_branch: str | None = None
    next_node_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def make(cls, *, decision: str, reason: str, gate_type: str,
             confidence: float = 0.5, needs_human: bool = False,
             triggered_by_seq: int = 0, **kwargs) -> "SupervisorDecision":
        return cls(
            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
            decision=decision,
            reason=reason,
            confidence=confidence,
            needs_human=needs_human,
            timestamp=datetime.now(timezone.utc).isoformat(),
            gate_type=gate_type,
            triggered_by_seq=triggered_by_seq,
            **kwargs,
        )


@dataclass
class HandoffInstruction:
    """First-class instruction sent to the agent."""
    instruction_id: str
    timestamp: str
    content: str
    node_id: str
    current_attempt: int
    triggered_by_decision_id: str
    trigger_type: str          # "init" | "node_advance" | "retry" | "branch"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def make(cls, *, content: str, node_id: str, current_attempt: int,
             triggered_by_decision_id: str, trigger_type: str) -> "HandoffInstruction":
        return cls(
            instruction_id=f"ins_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            content=content,
            node_id=node_id,
            current_attempt=current_attempt,
            triggered_by_decision_id=triggered_by_decision_id,
            trigger_type=trigger_type,
        )


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
    completed_reviews: list[str] = field(default_factory=list)
    # P0-B: event-driven injection tracking
    last_injected_node_id: str | None = None
    last_injected_attempt: int = -1
    # P0-C: checkpoint sequence tracking
    checkpoint_seq: int = 0
    # P1-D: resume validation
    spec_path: str = ""
    spec_hash: str = ""
    pane_target: str = ""
    surface_type: str = "tmux"
    workspace_root: str = ""
    auto_intervention_count: int = 0
    node_mismatch_count: int = 0
    last_mismatch_node_id: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupervisorState":
        rb = data.get("retry_budget", {})
        return cls(
            run_id=data["run_id"],
            spec_id=data["spec_id"],
            mode=data["mode"],
            top_state=normalize_top_state(data["top_state"]),
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
            completed_reviews=data.get("completed_reviews", []),
            last_injected_node_id=data.get("last_injected_node_id"),
            last_injected_attempt=data.get("last_injected_attempt", -1),
            checkpoint_seq=data.get("checkpoint_seq", 0),
            spec_path=data.get("spec_path", ""),
            spec_hash=data.get("spec_hash", ""),
            pane_target=data.get("pane_target", ""),
            surface_type=data.get("surface_type", "tmux"),
            workspace_root=data.get("workspace_root", ""),
            auto_intervention_count=data.get("auto_intervention_count", 0),
            node_mismatch_count=data.get("node_mismatch_count", 0),
            last_mismatch_node_id=data.get("last_mismatch_node_id", ""),
            schema_version=data.get("schema_version", 1),
        )
