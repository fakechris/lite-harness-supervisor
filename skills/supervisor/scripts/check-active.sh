#!/bin/bash
# Stop hook: prevents agent from stopping while supervisor has active work.
# Returns JSON that Claude Code's hook system understands.

set -euo pipefail

STATE_FILE=".supervisor/runtime/state.json"

if [ ! -f "$STATE_FILE" ]; then
    # No supervisor run — allow stop
    exit 0
fi

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
        # Final state or unreadable — allow stop
        exit 0
        ;;
    *)
        # Active run — block stop
        echo "Supervisor run is active (state=$STATUS). Continue working on the current step."
        exit 2
        ;;
esac
