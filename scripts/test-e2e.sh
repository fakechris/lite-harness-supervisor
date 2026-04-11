#!/bin/bash
# End-to-end test: run Codex in tmux with supervisor sidecar
#
# Prerequisites:
#   pip install -e .
#   codex CLI installed
#   tmux installed
#
# Usage:
#   ./scripts/test-e2e.sh
#
# This creates a tmux session with two panes:
#   Pane 0: Codex (agent) — user interacts here
#   Pane 1: Supervisor logs — watch supervisor decisions
#
# After setup, attach to the session and invoke /supervisor skill in Codex.

set -euo pipefail

SESSION="sv-test"
SPEC="specs/examples/linear_plan.example.yaml"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info() { printf "${GREEN}[test-e2e]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[test-e2e]${NC} %s\n" "$*"; }

# Check prerequisites
command -v thin-supervisor >/dev/null || { warn "thin-supervisor not found. Run: pip install -e ."; exit 1; }
command -v tmux >/dev/null || { warn "tmux not found."; exit 1; }

# Kill existing session if any
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Initialize supervisor in project dir
cd "$(dirname "$0")/.."
thin-supervisor init --force 2>/dev/null

info "Creating tmux session '$SESSION'..."

# Create session with first pane (agent pane)
tmux new-session -d -s "$SESSION" -x 200 -y 50

# Label the agent pane
tmux set-option -p -t "$SESSION:0.0" @name "agent"

# Split horizontally for supervisor logs pane
tmux split-window -h -t "$SESSION"
tmux set-option -p -t "$SESSION:0.1" @name "supervisor"

# In the supervisor pane, show instructions
tmux send-keys -t "$SESSION:0.1" "echo '=== Supervisor Pane ===' && echo 'Waiting for supervisor to start...'" Enter

# Focus the agent pane
tmux select-pane -t "$SESSION:0.0"

info "Session '$SESSION' created with 2 panes:"
info "  Pane 0 (agent):      Start codex here"
info "  Pane 1 (supervisor): Supervisor logs will appear here"
info ""
info "Next steps:"
info "  1. Attach:  tmux attach -t $SESSION"
info "  2. In the agent pane, start codex:  codex"
info "  3. In codex, invoke the supervisor skill or describe a task"
info "  4. Once the spec exists, attach the supervisor:"
info "     scripts/thin-supervisor-attach.sh $(basename "$SPEC" .yaml)"
info "  5. Or foreground (in supervisor pane):"
info "     thin-supervisor run foreground --spec $SPEC --pane agent"
info ""
info "Monitor:"
info "  thin-supervisor status"
info "  thin-supervisor bridge read agent 50"
info "  tail -f .supervisor/runtime/supervisor.log"
info ""
info "Stop:"
info "  thin-supervisor stop"
info "  tmux kill-session -t $SESSION"
