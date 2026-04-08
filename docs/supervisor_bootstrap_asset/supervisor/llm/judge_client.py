class JudgeClient:
    '''
    V1 stub:
    - 先保留成本地规则优先
    - 当规则无法定论时，返回保守 JSON
    - 后续可替换成真正的小模型调用
    '''
    def continue_or_escalate(self, context: dict) -> dict:
        return {
            "decision": "continue",
            "reason": "fallback stub prefers continue",
            "confidence": 0.51,
            "needs_human": False,
            "next_instruction": (
                "Continue with the highest-priority remaining action in the current node. "
                "Do not ask the user for confirmation unless blocked by missing authority, "
                "missing external input, or destructive irreversible action."
            ),
        }

    def choose_branch(self, context: dict) -> dict:
        return {
            "decision": "escalate_to_human",
            "reason": "branch stub not implemented",
            "confidence": 0.2,
            "needs_human": True,
        }

    def finish_or_continue(self, context: dict) -> dict:
        return {
            "decision": "continue",
            "reason": "finish stub defaults to verifier-driven flow",
            "confidence": 0.5,
            "needs_human": False,
        }
