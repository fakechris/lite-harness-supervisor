from __future__ import annotations
import subprocess

class GitVerifier:
    def run(self, check: dict) -> dict:
        mode = check.get("check", "dirty")
        if mode == "dirty":
            result = subprocess.run("git status --porcelain", shell=True, text=True, capture_output=True)
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
