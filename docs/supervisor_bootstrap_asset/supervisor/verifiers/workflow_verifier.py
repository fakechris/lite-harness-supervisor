from __future__ import annotations

class WorkflowVerifier:
    def run(self, check: dict, context: dict) -> dict:
        require_node_done = check.get("require_node_done", False)
        done = context.get("current_node_done", False)
        ok = (not require_node_done) or done
        return {
            "type": "workflow",
            "ok": ok,
            "require_node_done": require_node_done,
            "current_node_done": done,
        }
