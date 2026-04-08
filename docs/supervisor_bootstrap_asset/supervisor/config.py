from dataclasses import dataclass

@dataclass
class RuntimeConfig:
    runtime_dir: str = "runtime"
    state_file: str = "runtime/state.json"
    event_log_file: str = "runtime/event_log.jsonl"
    decision_log_file: str = "runtime/decision_log.jsonl"
    branch_confidence_threshold: float = 0.75
    default_agent_timeout_sec: int = 300
