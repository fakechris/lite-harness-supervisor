from enum import Enum

class TopState(str, Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    GATING = "GATING"
    VERIFYING = "VERIFYING"
    RECOVERY_NEEDED = "RECOVERY_NEEDED"
    PAUSED_FOR_HUMAN = "PAUSED_FOR_HUMAN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"

class DeliveryState(str, Enum):
    IDLE = "IDLE"                         # no pending injection
    INJECTED = "INJECTED"                 # send-keys completed
    SUBMITTED = "SUBMITTED"               # text left input area (clean snapshot)
    ACKNOWLEDGED = "ACKNOWLEDGED"         # agent shows processing indicators
    STARTED_PROCESSING = "STARTED_PROCESSING"  # checkpoint with seq > injection seq
    FAILED = "FAILED"                     # injection error or observation-only
    TIMED_OUT = "TIMED_OUT"               # 60s passed, no checkpoint

class DecisionType(str, Enum):
    CONTINUE = "CONTINUE"
    RETRY = "RETRY"
    VERIFY_STEP = "VERIFY_STEP"
    ADVANCE_STEP = "ADVANCE_STEP"
    BRANCH = "BRANCH"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    FINISH = "FINISH"
    ABORT = "ABORT"
