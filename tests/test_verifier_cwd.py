"""Tests for verifier cwd support."""
import subprocess
from unittest.mock import patch, MagicMock

from supervisor.verifiers.command_verifier import CommandVerifier
from supervisor.verifiers.git_verifier import GitVerifier
from supervisor.verifiers.artifact_verifier import ArtifactVerifier
from supervisor.verifiers.suite import VerifierSuite


def test_command_verifier_passes_cwd():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        cv = CommandVerifier()
        cv.run({"run": "echo ok", "expect": "pass"}, cwd="/tmp/test")
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["cwd"] == "/tmp/test"


def test_git_verifier_passes_cwd():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        gv = GitVerifier()
        gv.run({"check": "dirty", "expect": False}, cwd="/tmp/repo")
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["cwd"] == "/tmp/repo"


def test_artifact_verifier_uses_cwd(tmp_path):
    # Create file in a specific directory
    (tmp_path / "test.txt").write_text("hello")
    av = ArtifactVerifier()

    # Without cwd: file not found (relative path)
    result = av.run({"path": "test.txt", "exists": True})
    # This may or may not find it depending on cwd

    # With cwd: should find it
    result = av.run({"path": "test.txt", "exists": True}, cwd=str(tmp_path))
    assert result["ok"] is True


def test_suite_passes_cwd():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        suite = VerifierSuite()
        from supervisor.domain.models import VerifyCheck
        checks = [VerifyCheck(type="command", payload={"run": "echo ok", "expect": "pass"})]
        suite.run(checks, {}, cwd="/tmp/project")
        assert mock_run.call_args.kwargs["cwd"] == "/tmp/project"
