from __future__ import annotations

from pathlib import Path
import re


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
    _assert_skill_split(ROOT / "packaging" / "thin-supervisor-codex")


def test_project_local_skill_names_are_unique():
    seen: dict[str, Path] = {}
    for md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        text = md.read_text(encoding="utf-8")
        match = re.search(r"^name:\s*(.+)$", text, re.M)
        assert match, f"missing name frontmatter in {md}"
        skill_name = match.group(1).strip()
        if skill_name in seen:
            raise AssertionError(
                f"duplicate project-local skill name {skill_name!r}: {seen[skill_name]} and {md}"
            )
        seen[skill_name] = md
