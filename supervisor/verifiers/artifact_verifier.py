from __future__ import annotations
from pathlib import Path


class ArtifactVerifier:
    def run(self, check: dict, *, cwd: str | None = None) -> dict:
        raw_path = check["path"]
        if cwd:
            path = Path(cwd) / raw_path
        else:
            path = Path(raw_path)
        expected = check.get("exists", True)
        actual = path.exists()
        return {
            "type": "artifact",
            "ok": actual == expected,
            "path": str(raw_path),
            "exists": actual,
            "expected": expected,
        }
