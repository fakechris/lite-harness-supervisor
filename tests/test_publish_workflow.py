from pathlib import Path

import yaml


def test_publish_workflow_uses_staged_pypi_release_flow():
    workflow = yaml.safe_load(
        Path(".github/workflows/publish.yml").read_text(encoding="utf-8")
    )

    trigger = workflow.get("on", workflow.get(True))
    assert trigger is not None
    assert "workflow_dispatch" in trigger

    jobs = workflow["jobs"]
    assert "build" in jobs
    assert "publish-testpypi" in jobs
    assert "publish-pypi" in jobs

    assert jobs["publish-testpypi"]["environment"]["name"] == "testpypi"
    assert jobs["publish-pypi"]["environment"]["name"] == "pypi"

    build_steps = jobs["build"]["steps"]
    assert any(step.get("run") == "python -m build" for step in build_steps)
    assert any("actions/upload-artifact" in step.get("uses", "") for step in build_steps)

    publish_test_steps = jobs["publish-testpypi"]["steps"]
    assert any("actions/download-artifact" in step.get("uses", "") for step in publish_test_steps)
    assert any(
        step.get("with", {}).get("repository-url") == "https://test.pypi.org/legacy/"
        for step in publish_test_steps
        if "with" in step
    )

    publish_pypi_steps = jobs["publish-pypi"]["steps"]
    assert any("actions/download-artifact" in step.get("uses", "") for step in publish_pypi_steps)
    assert any(
        "gh-action-pypi-publish" in step.get("uses", "")
        for step in publish_pypi_steps
    )
