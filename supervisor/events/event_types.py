from enum import Enum

class EventType(str, Enum):
    SESSION_STARTED = "session_started"
    AGENT_OUTPUT = "agent_output"
    AGENT_ASK = "agent_ask"
    AGENT_STOP = "agent_stop"
    TOOL_RESULT = "tool_result"
    VERIFICATION_REQUESTED = "verification_requested"
    VERIFICATION_FINISHED = "verification_finished"
    GATE_DECISION_MADE = "gate_decision_made"
    HUMAN_REPLY = "human_reply"
    TIMEOUT = "timeout"
    RUN_ABORTED = "run_aborted"
