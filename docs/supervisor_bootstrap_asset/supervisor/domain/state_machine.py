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


def normalize_top_state(value: str | TopState) -> TopState:
    if isinstance(value, TopState):
        return value
    if value in LEGACY_TOP_STATE_MAP:
        return LEGACY_TOP_STATE_MAP[value]
    return TopState(value)
