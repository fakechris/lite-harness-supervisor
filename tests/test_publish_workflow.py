from pathlib import Path

import yaml


def test_publish_workflow_uses_staged_pypi_release_flow():
    workflow = yaml.safe_load(
        Path(".github/workflows/publish.yml").read_text(encoding="utf-8")
    )

    trigger = workflow.get("on", workflow.get(True))
    assert trigger is not None
    assert set(trigger.keys()) == {"push"}
    assert trigger["push"]["tags"] == ["v*"]

    jobs = workflow["jobs"]
    assert "verify-main-tag" in jobs
    assert "build" in jobs
    assert "publish-pypi" in jobs

    assert jobs["publish-pypi"]["environment"]["name"] == "pypi"

    verify_steps = jobs["verify-main-tag"]["steps"]
    assert any(
        step.get("with", {}).get("fetch-depth") == 0
        for step in verify_steps
        if "with" in step
    )
    assert any("git fetch origin main" in step.get("run", "") for step in verify_steps)
    assert any("git merge-base --is-ancestor" in step.get("run", "") for step in verify_steps)

    build_steps = jobs["build"]["steps"]
    assert any(step.get("run") == "python -m build" for step in build_steps)
    assert any("actions/upload-artifact" in step.get("uses", "") for step in build_steps)

    publish_pypi_steps = jobs["publish-pypi"]["steps"]
    assert any("actions/download-artifact" in step.get("uses", "") for step in publish_pypi_steps)
    assert any(
        "gh-action-pypi-publish" in step.get("uses", "")
        for step in publish_pypi_steps
    )
