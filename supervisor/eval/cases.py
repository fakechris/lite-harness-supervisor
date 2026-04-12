from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EvalCase:
    case_id: str
    category: str
    conversation: list[dict]
    expected: dict
    user_profile: dict = field(default_factory=dict)
    anti_goals: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "EvalCase":
        return cls(
            case_id=data["case_id"],
            category=data["category"],
            conversation=list(data.get("conversation") or []),
            expected=dict(data.get("expected") or {}),
            user_profile=dict(data.get("user_profile") or {}),
            anti_goals=list(data.get("anti_goals") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class EvalSuite:
    name: str
    cases: list[EvalCase]
    source_path: str = ""


def _bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "goldens"


def bundled_suite_path(name: str) -> Path:
    return _bundled_dir() / f"{name}.jsonl"


def list_bundled_suites() -> list[str]:
    root = _bundled_dir()
    if not root.exists():
        return []
    return sorted(path.stem for path in root.glob("*.jsonl"))


def load_eval_suite(path_or_name: str | Path) -> EvalSuite:
    path = Path(path_or_name)
    if not path.exists():
        path = bundled_suite_path(str(path_or_name))
    if not path.exists():
        raise FileNotFoundError(f"eval suite not found: {path_or_name}")

    cases: list[EvalCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        cases.append(EvalCase.from_dict(json.loads(line)))
    return EvalSuite(name=path.stem, cases=cases, source_path=str(path))


def save_eval_suite(suite: EvalSuite, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for case in suite.cases:
            handle.write(
                json.dumps(
                    {
                        "case_id": case.case_id,
                        "category": case.category,
                        "conversation": case.conversation,
                        "expected": case.expected,
                        "user_profile": case.user_profile,
                        "anti_goals": case.anti_goals,
                        "metadata": case.metadata,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return path
