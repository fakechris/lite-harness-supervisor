from __future__ import annotations

import json
import os
from pathlib import Path
from urllib import request as urllib_request

from supervisor.domain.models import OracleOpinion


DEFAULT_MODELS = {
    "openai": "o3",
    "deepseek": "deepseek-reasoner",
    "anthropic": "claude-sonnet-4-20250514",
}


class OracleClient:
    """Lightweight external consultation client for second-opinion workflows."""

    @staticmethod
    def detect_provider(preferred: str = "auto") -> tuple[str, str]:
        if preferred and preferred != "auto":
            key = {
                "openai": "OPENAI_API_KEY",
                "deepseek": "DEEPSEEK_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
            }.get(preferred, "")
            if key and os.environ.get(key):
                return preferred, DEFAULT_MODELS[preferred]
            return "", ""

        if os.environ.get("OPENAI_API_KEY"):
            return "openai", DEFAULT_MODELS["openai"]
        if os.environ.get("DEEPSEEK_API_KEY"):
            return "deepseek", DEFAULT_MODELS["deepseek"]
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic", DEFAULT_MODELS["anthropic"]
        return "", ""

    def consult(
        self,
        *,
        question: str,
        file_paths: list[str],
        mode: str = "review",
        provider: str = "auto",
    ) -> OracleOpinion:
        normalized_files = [str(Path(p)) for p in file_paths]
        file_context = self._load_file_context(normalized_files)
        selected_provider, model_name = self.detect_provider(provider)
        prompt = self._build_prompt(question=question, file_context=file_context, mode=mode)

        if not selected_provider:
            return OracleOpinion(
                provider="self-review",
                model_name="self-review",
                mode=mode,
                question=question,
                files=normalized_files,
                response_text=self._fallback_response(question, normalized_files),
                source="fallback",
            )

        response_text = self._call_provider(selected_provider, model_name, prompt)
        return OracleOpinion(
            provider=selected_provider,
            model_name=model_name,
            mode=mode,
            question=question,
            files=normalized_files,
            response_text=response_text,
            source="external",
        )

    def _load_file_context(self, file_paths: list[str], *, max_chars: int = 12000) -> list[dict[str, str]]:
        context: list[dict[str, str]] = []
        for file_path in file_paths:
            path = Path(file_path)
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                content = "<unreadable>"
            if len(content) > max_chars:
                content = content[:max_chars] + "\n...<truncated>..."
            context.append({"path": str(path), "content": content})
        return context

    def _build_prompt(self, *, question: str, file_context: list[dict[str, str]], mode: str) -> str:
        sections = [
            "You are an independent senior engineer providing an advisory second opinion.",
            f"## Consultation Mode\n{mode}",
            f"## Question\n{question}",
            "## Relevant Files",
        ]
        if file_context:
            for item in file_context:
                sections.append(f"### {item['path']}\n```text\n{item['content']}\n```")
        else:
            sections.append("(no files provided)")
        sections.append(
            "## What I Need From You\n"
            "1. Independent analysis\n"
            "2. Recommended next steps\n"
            "3. Risks, edge cases, or failure modes\n"
            "4. Suggested verification"
        )
        return "\n\n".join(sections)

    def _fallback_response(self, question: str, file_paths: list[str]) -> str:
        files = ", ".join(file_paths) if file_paths else "(no files)"
        return (
            "No external oracle provider is configured. Falling back to a self-adversarial review scaffold.\n"
            f"Question: {question}\n"
            f"Files: {files}\n"
            "Check these before acting:\n"
            "1. What assumption in the current approach is most likely to be wrong?\n"
            "2. What is the highest-impact failure mode or regression risk?\n"
            "3. What single verification command would most quickly falsify the plan?"
        )

    def _call_provider(self, provider: str, model_name: str, prompt: str) -> str:
        if provider == "openai":
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_completion_tokens": 4096,
            }
            data = self._post_json(
                "https://api.openai.com/v1/chat/completions",
                {
                    "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                    "Content-Type": "application/json",
                },
                payload,
            )
            return data["choices"][0]["message"]["content"]

        if provider == "deepseek":
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
            }
            data = self._post_json(
                "https://api.deepseek.com/chat/completions",
                {
                    "Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}",
                    "Content-Type": "application/json",
                },
                payload,
            )
            return data["choices"][0]["message"]["content"]

        if provider == "anthropic":
            payload = {
                "model": model_name,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }
            data = self._post_json(
                "https://api.anthropic.com/v1/messages",
                {
                    "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                payload,
            )
            return data["content"][0]["text"]

        raise ValueError(f"unsupported oracle provider: {provider}")

    @staticmethod
    def _post_json(url: str, headers: dict[str, str], payload: dict) -> dict:
        req = urllib_request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
