from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _assert_skill_split(skill_dir: Path) -> None:
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    assert "references/contract.md" in skill_text
    assert "strategy/approval-boundary.md" in skill_text
    assert "strategy/finish-proof.md" in skill_text
    assert "strategy/escalation.md" in skill_text
    assert "strategy/pause-ux.md" in skill_text

    assert (skill_dir / "references" / "contract.md").exists()
    assert (skill_dir / "strategy" / "approval-boundary.md").exists()
    assert (skill_dir / "strategy" / "finish-proof.md").exists()
    assert (skill_dir / "strategy" / "escalation.md").exists()
    assert (skill_dir / "strategy" / "pause-ux.md").exists()


def test_claude_skill_uses_contract_and_strategy_split():
    _assert_skill_split(ROOT / "skills" / "thin-supervisor")


def test_codex_skill_uses_contract_and_strategy_split():
    _assert_skill_split(ROOT / "skills" / "thin-supervisor-codex")
