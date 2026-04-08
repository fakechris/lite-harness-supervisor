#!/bin/bash
# Stop hook: prevents agent from stopping while supervisor has active work.
# Checks both PID file (daemon alive?) and state.json (run active?).

set -euo pipefail

PID_FILE=".supervisor/runtime/supervisor.pid"
STATE_FILE=".supervisor/runtime/state.json"

# Check if supervisor daemon is running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE" 2>/dev/null || echo "0")
    if kill -0 "$PID" 2>/dev/null; then
        # Daemon is alive — check state
        if [ -f "$STATE_FILE" ]; then
            STATUS=$(python3 -c "
import json, sys
try:
    state = json.load(open('$STATE_FILE'))
    print(state.get('top_state', 'UNKNOWN'))
except Exception:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

            case "$STATUS" in
                COMPLETED|FAILED|ABORTED|UNKNOWN)
                    exit 0
                    ;;
                *)
                    echo "Supervisor run is active (state=$STATUS, daemon PID=$PID). Continue working on the current step."
                    exit 2
                    ;;
            esac
        fi
    fi
fi

# No daemon or no state — allow stop
exit 0
