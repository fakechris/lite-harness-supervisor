"""LLM-backed explainer for operator-facing explanations and drift assessment.

Separate from the judge: the judge serves deterministic gating (conservative,
low-call-volume), while the explainer serves human understanding (tolerates
approximation, may use cheaper/faster models).

When ``model`` is ``None`` the client runs in **stub mode** — returns
structured fallbacks derived from raw state, no LLM calls.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPT_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _parse_json(text: str) -> dict:
    """Extract the first JSON object from *text*."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [line for line in lines if not line.startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise


class ExplainerClient:
    """Operator-facing explainer — separate model from judge.

    Parameters
    ----------
    model : str | None
        A LiteLLM model identifier for routine explanations.
        Set to ``None`` for stub mode.
    temperature : float
        Sampling temperature (higher than judge — explanations are softer).
    max_tokens : int
        Max response tokens (larger than judge — explanations are longer).
    """

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def explain_run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Explain what a run is currently doing."""
        if self.model is None:
            return self._stub_explain_run(context)
        fallback = self._stub_explain_run(context)
        try:
            prompt = _load_prompt("explain_run.txt")
        except FileNotFoundError:
            logger.warning("explain_run prompt not found, falling back to stub")
            return fallback
        return self._call(prompt, context, fallback=fallback)

    def explain_exchange(self, context: dict[str, Any]) -> dict[str, Any]:
        """Explain a recent supervisor/worker exchange."""
        if self.model is None:
            return self._stub_explain_exchange(context)
        fallback = self._stub_explain_exchange(context)
        try:
            prompt = _load_prompt("explain_exchange.txt")
        except FileNotFoundError:
            logger.warning("explain_exchange prompt not found, falling back to stub")
            return fallback
        return self._call(prompt, context, fallback=fallback)

    def assess_drift(self, context: dict[str, Any]) -> dict[str, Any]:
        """Assess whether a run is drifting from its approved plan."""
        if self.model is None:
            return self._stub_assess_drift(context)
        fallback = self._stub_assess_drift(context)
        try:
            prompt = _load_prompt("assess_drift.txt")
        except FileNotFoundError:
            logger.warning("assess_drift prompt not found, falling back to stub")
            return fallback
        return self._call(prompt, context, fallback=fallback)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call(
        self,
        system_prompt: str,
        context: dict[str, Any],
        *,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            import litellm
        except ImportError:
            logger.warning("litellm not installed, falling back to stub")
            return fallback

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        try:
            response = litellm.completion(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            text = response.choices[0].message.content
            result = _parse_json(text)
            if not isinstance(result, dict):
                return fallback
            return result
        except Exception:
            logger.exception("Explainer LLM call failed, falling back to stub")
            return fallback

    # ------------------------------------------------------------------
    # Stubs — structured fallbacks from raw state (no LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def _stub_explain_run(context: dict[str, Any]) -> dict[str, Any]:
        state = context.get("run_state", {})
        lang = context.get("language", "en")
        zh = lang == "zh"
        top_state = state.get("top_state", "UNKNOWN")
        current_node = state.get("current_node_id", "")
        done = state.get("done_node_ids", [])
        cp = state.get("last_agent_checkpoint", {})
        cp_summary = cp.get("summary", "") if isinstance(cp, dict) else ""

        if zh:
            activity = f"状态: {top_state}, 节点: {current_node}"
            if cp_summary:
                activity += f" — {cp_summary}"
            done_str = ", ".join(done) if done else "(无)"
            return {
                "explanation": (
                    f"运行处于 {top_state} 状态，当前节点 '{current_node}'。"
                    f"已完成节点: {done_str}。"
                    + (f" 最近检查点: {cp_summary}" if cp_summary else "")
                ),
                "current_activity": activity,
                "recent_progress": f"已完成 {len(done)} 个节点: {done_str}" if done else "尚未完成任何节点",
                "next_expected": "等待 worker 的下一个检查点",
                "confidence": 0.3,
            }

        activity = f"State: {top_state}, node: {current_node}"
        if cp_summary:
            activity += f" — {cp_summary}"

        return {
            "explanation": (
                f"Run is in {top_state} state at node '{current_node}'. "
                f"Completed nodes: {done or '(none)'}."
                + (f" Last checkpoint: {cp_summary}" if cp_summary else "")
            ),
            "current_activity": activity,
            "recent_progress": f"Completed {len(done)} node(s): {', '.join(done)}" if done else "No nodes completed yet",
            "next_expected": "Awaiting next checkpoint from worker",
            "confidence": 0.3,
        }

    @staticmethod
    def _stub_explain_exchange(context: dict[str, Any]) -> dict[str, Any]:
        exchange = context.get("exchange", {})
        lang = context.get("language", "en")
        zh = lang == "zh"
        cp = exchange.get("last_checkpoint_summary", "")
        instr = exchange.get("last_instruction_summary", "")

        if zh:
            return {
                "explanation": (
                    f"Worker 检查点: {cp or '(无)'}。"
                    f"Supervisor 指令: {instr or '(无)'}。"
                ),
                "worker_intent": cp or "(无检查点)",
                "supervisor_response": instr or "(无指令)",
                "outcome": "交换细节请查看原始时间线事件",
                "confidence": 0.2,
            }

        return {
            "explanation": (
                f"Worker checkpoint: {cp or '(none)'}. "
                f"Supervisor instruction: {instr or '(none)'}."
            ),
            "worker_intent": cp or "(no checkpoint available)",
            "supervisor_response": instr or "(no instruction available)",
            "outcome": "Exchange details available in raw timeline events",
            "confidence": 0.2,
        }

    @staticmethod
    def _stub_assess_drift(context: dict[str, Any]) -> dict[str, Any]:
        state = context.get("run_state", {})
        lang = context.get("language", "en")
        zh = lang == "zh"
        retry_budget = state.get("retry_budget", {})
        used_global = retry_budget.get("used_global", 0) if isinstance(retry_budget, dict) else 0
        mismatch = state.get("node_mismatch_count", 0)
        auto_interventions = state.get("auto_intervention_count", 0)

        reasons: list[str] = []
        if zh:
            if used_global > 3:
                reasons.append(f"重试次数较多（已用 {used_global} 次全局重试）")
            if mismatch > 0:
                reasons.append(f"节点不匹配（{mismatch} 次）")
            if auto_interventions > 1:
                reasons.append(f"多次自动干预（{auto_interventions} 次）")

            if not reasons:
                status, action = "on_track", "无需操作"
            elif len(reasons) == 1 and used_global <= 5:
                status, action = "watch", "继续观察"
            else:
                status, action = "drifting", "建议检查运行状态并考虑暂停"
        else:
            if used_global > 3:
                reasons.append(f"High retry count ({used_global} global retries used)")
            if mismatch > 0:
                reasons.append(f"Node mismatch detected ({mismatch} times)")
            if auto_interventions > 1:
                reasons.append(f"Multiple auto-interventions ({auto_interventions})")

            if not reasons:
                status, action = "on_track", "No action needed"
            elif len(reasons) == 1 and used_global <= 5:
                status, action = "watch", "Monitor for further issues"
            else:
                status, action = "drifting", "Review run state and consider pausing"

        return {
            "status": status,
            "reasons": reasons or (["未检测到偏移信号"] if zh else ["No drift signals detected"]),
            "evidence": [
                f"retries_used={used_global}",
                f"node_mismatches={mismatch}",
                f"auto_interventions={auto_interventions}",
            ],
            "recommended_action": action,
            "confidence": 0.3,
        }
