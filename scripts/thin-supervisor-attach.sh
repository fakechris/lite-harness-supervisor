#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: scripts/thin-supervisor-attach.sh <slug-or-spec-path>" >&2
  exit 1
fi

input="$1"
if [[ "$input" == *.yaml || "$input" == *.yml || "$input" == */* ]]; then
  spec_path="$input"
else
  spec_path=".supervisor/specs/${input}.yaml"
fi

if [[ ! -f "$spec_path" ]]; then
  echo "error: spec not found: $spec_path" >&2
  exit 1
fi

mkdir -p .supervisor/runtime .supervisor/specs .supervisor/clarify .supervisor/plans
if [[ ! -f .supervisor/config.yaml ]]; then
  thin-supervisor init
fi

# Read surface_type from config (default: tmux)
surface_type="tmux"
if [[ -f .supervisor/config.yaml ]]; then
  st=$(grep "^surface_type:" .supervisor/config.yaml 2>/dev/null | awk '{print $2}' | tr -d '"' || true)
  [[ -n "$st" ]] && surface_type="$st"
fi

case "$surface_type" in
  tmux)
    pane_id="$(thin-supervisor bridge id)" || {
      echo "error: must run inside a tmux pane" >&2
      exit 1
    }
    thin-supervisor run register --spec "$spec_path" --pane "$pane_id"
    ;;
  jsonl)
    jsonl_path="$(thin-supervisor session jsonl)" || {
      echo "error: could not detect JSONL transcript" >&2
      exit 1
    }
    thin-supervisor run register --spec "$spec_path" --pane "$jsonl_path" --surface jsonl
    ;;
  open_relay)
    echo "error: open_relay requires manual attach:" >&2
    echo "  thin-supervisor run register --spec $spec_path --pane <oly-session-id> --surface open_relay" >&2
    exit 1
    ;;
  *)
    echo "error: unknown surface_type '$surface_type' in config" >&2
    exit 1
    ;;
esac
