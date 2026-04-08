class CodexRunner:
    '''
    V1 placeholder.
    真实集成时可替换为:
    - 调 Codex CLI
    - 订阅 stdout / transcript
    - 注入 next_instruction
    '''
    def run_one_iteration(self, prompt: str, run_id: str) -> dict:
        return {"status": "stubbed", "run_id": run_id, "prompt": prompt}

    def inject(self, instruction: str, run_id: str) -> dict:
        return {"status": "injected", "run_id": run_id, "instruction": instruction}
