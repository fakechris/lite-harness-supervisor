"""Tests for the supervisor attach helper script."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "lh-supervisor-attach.sh"


def test_attach_script_reports_clear_error_when_not_in_tmux(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    supervisor_dir = workspace / ".supervisor" / "specs"
    supervisor_dir.mkdir(parents=True)
    (supervisor_dir / "plan.yaml").write_text("kind: linear_plan\nid: plan\ngoal: test\nsteps: []\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    thin = bin_dir / "thin-supervisor"
    thin.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ \"$1\" == \"bridge\" && \"$2\" == \"id\" ]]; then\n"
        "  echo \"error: not inside tmux\" >&2\n"
        "  exit 1\n"
        "fi\n"
        "if [[ \"$1\" == \"init\" ]]; then\n"
        "  exit 0\n"
        "fi\n"
        "echo \"unexpected: $*\" >&2\n"
        "exit 99\n"
    )
    thin.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("TMUX", None)

    result = subprocess.run(
        [str(SCRIPT), "plan"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "must run inside a tmux pane" in result.stderr
