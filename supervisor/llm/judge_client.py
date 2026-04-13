"""LLM judge for gate decisions when rules are inconclusive.

Uses LiteLLM for provider-agnostic model calls.  When ``model`` is
``None`` the client runs in **stub mode** — no external calls, returns
conservative defaults.  This keeps the test suite runnable without API
keys.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from supervisor.protocol.checkpoints import sanitize_instruction_text

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPT_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _parse_json(text: str) -> dict:
    """Extract the first JSON object from *text*."""
    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON substring
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise


class JudgeClient:
    """Small-model judge for gate / branch / finish decisions.

    Parameters
    ----------
    model : str | None
        A LiteLLM model identifier, e.g.
        ``"anthropic/claude-haiku-4-5-20251001"`` or
        ``"openai/gpt-4o-mini"``.  Set to ``None`` for stub mode.
    temperature : float
        Sampling temperature (low = deterministic).
    max_tokens : int
        Max response tokens.
    """

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    # Public decision methods
    # ------------------------------------------------------------------

    def continue_or_escalate(self, context: dict) -> dict:
        if self.model is None:
            return self._stub_continue()
        prompt = _load_prompt("continue_or_escalate.txt")
        return self._call(
            prompt,
            context,
            allowed_decisions={"CONTINUE", "VERIFY_STEP", "RETRY", "ESCALATE_TO_HUMAN"},
            allow_next_instruction=True,
        )

    def choose_branch(self, context: dict) -> dict:
        if self.model is None:
            return self._stub_branch()
        prompt = _load_prompt("branch_decider.txt")
        return self._call(prompt, context)

    def finish_or_continue(self, context: dict) -> dict:
        if self.model is None:
            return self._stub_finish()
        prompt = _load_prompt("continue_or_escalate.txt")
        context = {**context, "_hint": "check if workflow can finish"}
        return self._call(
            prompt,
            context,
            allowed_decisions={"CONTINUE", "VERIFY_STEP", "RETRY", "ESCALATE_TO_HUMAN"},
            allow_next_instruction=True,
        )

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call(
        self,
        system_prompt: str,
        context: dict,
        *,
        allowed_decisions: set[str] | None = None,
        allow_next_instruction: bool = False,
    ) -> dict:
        try:
            import litellm
        except ImportError:
            logger.warning("litellm not installed, falling back to stub")
            return self._stub_continue()

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
            result = self._sanitize_result(
                _parse_json(text),
                allowed_decisions=allowed_decisions,
                allow_next_instruction=allow_next_instruction,
            )
            return result
        except Exception:
            logger.exception("LLM judge call failed, falling back to stub")
            return self._stub_continue()

    def _sanitize_result(
        self,
        result: dict,
        *,
        allowed_decisions: set[str] | None = None,
        allow_next_instruction: bool = False,
    ) -> dict:
        if not isinstance(result, dict):
            raise ValueError("judge result must be an object")

        decision = str(result.get("decision", "")).strip().upper()
        if allowed_decisions is not None and decision not in allowed_decisions:
            raise ValueError(f"invalid judge decision: {decision!r}")

        sanitized = {
            "decision": (decision or "CONTINUE").lower(),
            "reason": " ".join(str(result.get("reason", "")).split())[:400].rstrip(),
            "confidence": _clamp_confidence(result.get("confidence", 0.5)),
            "needs_human": bool(result.get("needs_human", False)),
        }
        if allow_next_instruction:
            sanitized["next_instruction"] = sanitize_instruction_text(result.get("next_instruction"))
        return sanitized

    # ------------------------------------------------------------------
    # Stubs (used when model is None or on error)
    # ------------------------------------------------------------------

    def _stub_continue(self) -> dict:
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

    def _stub_branch(self) -> dict:
        return {
            "decision": "escalate_to_human",
            "reason": "branch stub not implemented",
            "confidence": 0.2,
            "needs_human": True,
        }

    def _stub_finish(self) -> dict:
        return {
            "decision": "continue",
            "reason": "finish stub defaults to verifier-driven flow",
            "confidence": 0.5,
            "needs_human": False,
        }


def _clamp_confidence(value) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.5
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed
