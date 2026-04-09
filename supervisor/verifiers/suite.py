from __future__ import annotations

from supervisor.verifiers.command_verifier import CommandVerifier
from supervisor.verifiers.artifact_verifier import ArtifactVerifier
from supervisor.verifiers.git_verifier import GitVerifier
from supervisor.verifiers.workflow_verifier import WorkflowVerifier


class VerifierSuite:
    def __init__(self):
        self.command_verifier = CommandVerifier()
        self.artifact_verifier = ArtifactVerifier()
        self.git_verifier = GitVerifier()
        self.workflow_verifier = WorkflowVerifier()

    def run(self, verify_checks, context: dict, *, cwd: str | None = None) -> dict:
        results = []
        for check in verify_checks:
            payload = check.payload if hasattr(check, "payload") else check
            check_type = check.type if hasattr(check, "type") else check["type"]
            if check_type == "command":
                results.append(self.command_verifier.run(payload, cwd=cwd))
            elif check_type == "artifact":
                results.append(self.artifact_verifier.run(payload, cwd=cwd))
            elif check_type == "git":
                results.append(self.git_verifier.run(payload, cwd=cwd))
            elif check_type == "workflow":
                results.append(self.workflow_verifier.run(payload, context))
            else:
                raise ValueError(f"unsupported verifier type: {check_type}")
        return {"ok": all(x["ok"] for x in results), "results": results}
