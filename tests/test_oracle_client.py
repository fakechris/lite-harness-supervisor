from __future__ import annotations

import json
from pathlib import Path

from supervisor.domain.models import OracleOpinion
from supervisor.oracle.client import OracleClient


def test_oracle_opinion_auto_ids_and_serializes():
    opinion = OracleOpinion(
        provider="openai",
        model_name="o3",
        mode="review",
        question="What is wrong here?",
        files=["a.py"],
        response_text="Independent analysis",
    )

    assert opinion.consultation_id.startswith("oracle_")
    assert opinion.to_dict()["provider"] == "openai"
    assert opinion.timestamp


def test_detect_provider_prefers_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")

    assert OracleClient.detect_provider() == ("openai", "o3")


def test_consult_without_api_key_returns_self_review(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    target = tmp_path / "mod.py"
    target.write_text("def add(a, b):\n    return a + b\n")

    opinion = OracleClient().consult(
        question="Review this helper",
        file_paths=[str(target)],
        mode="review",
    )

    assert opinion.provider == "self-review"
    assert opinion.source == "fallback"
    assert str(target) in opinion.files
    assert "no external oracle provider" in opinion.response_text.lower()


def test_consult_with_provider_includes_file_context(tmp_path, monkeypatch):
    target = tmp_path / "mod.py"
    target.write_text("def add(a, b):\n    return a + b\n")

    client = OracleClient()

    monkeypatch.setattr(client, "detect_provider", lambda preferred="auto": ("openai", "o3"))

    captured: dict[str, str] = {}

    def fake_call(provider: str, model_name: str, prompt: str) -> str:
        captured["provider"] = provider
        captured["model_name"] = model_name
        captured["prompt"] = prompt
        return "External review says this is fine."

    monkeypatch.setattr(client, "_call_provider", fake_call)

    opinion = client.consult(
        question="Review this helper",
        file_paths=[str(target)],
        mode="review",
    )

    assert opinion.provider == "openai"
    assert opinion.source == "external"
    assert "mod.py" in captured["prompt"]
    assert "def add(a, b)" in captured["prompt"]
    assert opinion.response_text == "External review says this is fine."
