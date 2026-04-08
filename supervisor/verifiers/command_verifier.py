from __future__ import annotations
import re
import subprocess

# Patterns that require shell interpretation (env vars, pipes, redirects, subshells)
_SHELL_SYNTAX_RE = re.compile(r'[|;&<>$`(]|\w+=\S+\s')


class CommandVerifier:
    def run(self, check: dict) -> dict:
        cmd = check["run"]
        expect = check.get("expect", "pass")
        # Commands come from spec YAML (user-authored or skill-generated).
        # Use shell=True only when the command contains shell syntax that
        # cannot be executed directly (env vars, pipes, redirects, etc.).
        result = subprocess.run(
            cmd, shell=True, text=True, capture_output=True, timeout=300,
        )
        ok = self._match(result.returncode, result.stdout, result.stderr, expect)
        return {
            "type": "command",
            "ok": ok,
            "run": cmd,
            "expect": expect,
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }

    def _match(self, returncode: int, stdout: str, stderr: str, expect: str) -> bool:
        if expect == "pass":
            return returncode == 0
        if expect == "fail":
            return returncode != 0
        if expect.startswith("contains:"):
            needle = expect.split("contains:", 1)[1]
            return needle in stdout or needle in stderr
        raise ValueError(f"unsupported expect: {expect}")
