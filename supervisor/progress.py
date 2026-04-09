"""Progress artifact writer — handoff context per Anthropic Managed Agents."""
from __future__ import annotations

import json
from pathlib import Path


def write_progress(state, spec, runtime_dir: str) -> None:
    """Write a human/machine-readable progress snapshot."""
    total = len(spec.ordered_nodes())
    done = len(state.done_node_ids)
    pct = round(done / total * 100) if total > 0 else 0

    progress = {
        "run_id": state.run_id,
        "spec_id": state.spec_id,
        "state": state.top_state.value if hasattr(state.top_state, "value") else str(state.top_state),
        "current_node": state.current_node_id,
        "done_nodes": state.done_node_ids,
        "total_nodes": total,
        "done_count": done,
        "progress_pct": pct,
        "current_attempt": state.current_attempt,
        "escalations": len(state.human_escalations),
    }
    path = Path(runtime_dir) / "progress.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=2))
