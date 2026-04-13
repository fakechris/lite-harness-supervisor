from pathlib import Path


def test_main_test_workflow_does_not_ignore_control_plane_tests():
    workflow = Path(".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "--ignore=tests/test_daemon.py" not in workflow
    assert "--ignore=tests/test_collaboration.py" not in workflow
    assert "--ignore=tests/test_attach_script.py" not in workflow
