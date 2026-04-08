from __future__ import annotations
from pathlib import Path

class ArtifactVerifier:
    def run(self, check: dict) -> dict:
        path = Path(check["path"])
        expected = check.get("exists", True)
        actual = path.exists()
        return {
            "type": "artifact",
            "ok": actual == expected,
            "path": str(path),
            "exists": actual,
            "expected": expected,
        }
