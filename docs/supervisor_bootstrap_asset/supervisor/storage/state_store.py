from __future__ import annotations
import json
import uuid
from pathlib import Path

from supervisor.domain.enums import TopState
from supervisor.domain.models import SupervisorState, WorkflowSpec

class StateStore:
    def __init__(self, runtime_dir: str = "runtime"):
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "state.json"
        self.event_log_path = self.runtime_dir / "event_log.jsonl"
        self.decision_log_path = self.runtime_dir / "decision_log.jsonl"

    def load_or_init(self, spec: WorkflowSpec) -> SupervisorState:
        if self.state_path.exists():
            return SupervisorState.from_dict(json.loads(self.state_path.read_text()))
        state = SupervisorState(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            spec_id=spec.id,
            mode=spec.kind,
            top_state=TopState.READY,
            current_node_id=spec.first_node_id(),
        )
        state.retry_budget.per_node = spec.policy.max_retries_per_node
        state.retry_budget.global_limit = spec.policy.max_retries_global
        self.save(state)
        return state

    def save(self, state: SupervisorState) -> None:
        self.state_path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))

    def append_event(self, event: dict) -> None:
        with self.event_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append_decision(self, decision: dict) -> None:
        with self.decision_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(decision, ensure_ascii=False) + "\n")
