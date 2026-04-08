from supervisor.domain.enums import TopState

FINAL_STATES = {
    TopState.COMPLETED,
    TopState.FAILED,
    TopState.ABORTED,
}
