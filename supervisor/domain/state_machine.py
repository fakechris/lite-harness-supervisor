from __future__ import annotations

from supervisor.domain.enums import TopState

FINAL_STATES = {
    TopState.COMPLETED,
    TopState.FAILED,
    TopState.ABORTED,
}

LEGACY_TOP_STATE_MAP = {
    "INIT": TopState.READY,
    "AWAITING_AGENT_EVENT": TopState.RUNNING,
}

ALLOWED_TOP_STATE_TRANSITIONS: dict[TopState, set[TopState]] = {
    TopState.READY: {
        TopState.RUNNING,
        TopState.GATING,
        TopState.VERIFYING,
        TopState.PAUSED_FOR_HUMAN,
        TopState.COMPLETED,
        TopState.FAILED,
        TopState.ABORTED,
    },
    TopState.RUNNING: {
        TopState.GATING,
        TopState.VERIFYING,
        TopState.PAUSED_FOR_HUMAN,
        TopState.COMPLETED,
        TopState.FAILED,
        TopState.ABORTED,
    },
    TopState.GATING: {
        TopState.RUNNING,
        TopState.VERIFYING,
        TopState.PAUSED_FOR_HUMAN,
        TopState.COMPLETED,
        TopState.FAILED,
        TopState.ABORTED,
    },
    TopState.VERIFYING: {
        TopState.RUNNING,
        TopState.PAUSED_FOR_HUMAN,
        TopState.COMPLETED,
        TopState.FAILED,
        TopState.ABORTED,
    },
    TopState.PAUSED_FOR_HUMAN: {
        TopState.RUNNING,
        TopState.COMPLETED,
        TopState.FAILED,
        TopState.ABORTED,
    },
    TopState.COMPLETED: set(),
    TopState.FAILED: set(),
    TopState.ABORTED: set(),
}


class InvalidTopStateTransition(ValueError):
    pass


def normalize_top_state(value: str | TopState) -> TopState:
    if isinstance(value, TopState):
        return value
    if value in LEGACY_TOP_STATE_MAP:
        return LEGACY_TOP_STATE_MAP[value]
    return TopState(value)


def can_transition(from_state: TopState | str, to_state: TopState | str) -> bool:
    from_value = normalize_top_state(from_state)
    to_value = normalize_top_state(to_state)
    if from_value == to_value:
        return True
    return to_value in ALLOWED_TOP_STATE_TRANSITIONS[from_value]


def transition_top_state(state, to_state: TopState | str, *, reason: str = "") -> TopState:
    current = normalize_top_state(state.top_state)
    target = normalize_top_state(to_state)
    if current != target and target not in ALLOWED_TOP_STATE_TRANSITIONS[current]:
        detail = f"invalid top_state transition {current.value} -> {target.value}"
        if reason:
            detail = f"{detail} ({reason})"
        raise InvalidTopStateTransition(detail)
    state.top_state = target
    return target
