"""Tests for the supervisor attach helper script."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "thin-supervisor-attach.sh"


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


def test_attach_script_repairs_partial_supervisor_before_registering(tmp_path):
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
        "log_file=\"${THIN_SUPERVISOR_TEST_LOG:?}\"\n"
        "printf '%s\\n' \"$*\" >> \"$log_file\"\n"
        "if [[ \"$1\" == \"init\" && \"$2\" == \"--repair\" ]]; then\n"
        "  mkdir -p .supervisor/runtime\n"
        "  cat > .supervisor/config.yaml <<'EOF'\n"
        "surface_type: \"tmux\"\n"
        "EOF\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$1\" == \"bridge\" && \"$2\" == \"id\" ]]; then\n"
        "  echo \"%42\"\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$1\" == \"run\" && \"$2\" == \"register\" ]]; then\n"
        "  exit 0\n"
        "fi\n"
        "echo \"unexpected: $*\" >&2\n"
        "exit 99\n"
    )
    thin.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["THIN_SUPERVISOR_TEST_LOG"] = str(tmp_path / "thin.log")
    env["TMUX"] = "1"

    result = subprocess.run(
        [str(SCRIPT), "plan"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    log_lines = (tmp_path / "thin.log").read_text().splitlines()
    assert "init --repair" in log_lines
    assert "bridge id" in log_lines
    assert "run register --spec .supervisor/specs/plan.yaml --pane %42" in log_lines
