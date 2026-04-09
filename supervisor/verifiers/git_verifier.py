from __future__ import annotations
import subprocess


class GitVerifier:
    def run(self, check: dict, *, cwd: str | None = None) -> dict:
        mode = check.get("check", "dirty")
        if mode == "dirty":
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                text=True, capture_output=True, cwd=cwd,
            )
            if result.returncode != 0:
                return {
                    "type": "git",
                    "ok": False,
                    "check": "dirty",
                    "reason": f"git status failed (rc={result.returncode}): {result.stderr.strip()}",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            dirty = bool(result.stdout.strip())
            expect = check.get("expect", True)
            return {
                "type": "git",
                "ok": dirty == expect,
                "check": "dirty",
                "dirty": dirty,
                "expected": expect,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        raise ValueError(f"unsupported git check: {mode}")
