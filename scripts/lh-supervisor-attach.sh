#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: scripts/lh-supervisor-attach.sh <slug-or-spec-path>" >&2
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

pane_id="$(thin-supervisor bridge id)" || {
  echo "error: must run inside a tmux pane" >&2
  exit 1
}
thin-supervisor run register --spec "$spec_path" --pane "$pane_id"
