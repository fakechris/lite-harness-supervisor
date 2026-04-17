from pathlib import Path

import yaml


def test_publish_workflow_requires_tag_on_main_head_and_only_publishes_to_pypi():
    workflow = yaml.safe_load(
        Path(".github/workflows/publish.yml").read_text(encoding="utf-8")
    )

    trigger = workflow.get("on", workflow.get(True))
    assert trigger is not None
    assert trigger == {"push": {"tags": ["v*"]}}

    jobs = workflow["jobs"]
    assert "build" in jobs
    assert "publish-pypi" in jobs

    assert jobs["publish-pypi"]["environment"]["name"] == "pypi"

    build_steps = jobs["build"]["steps"]
    assert any(step.get("with", {}).get("fetch-depth") == 0 for step in build_steps)
    validate_steps = [
        step for step in build_steps
        if step.get("name") == "Validate tag points to current main HEAD"
    ]
    assert len(validate_steps) == 1
    assert "origin/main" in validate_steps[0]["run"]
    assert "--tags" not in validate_steps[0]["run"]
    assert any(step.get("run") == "python -m build" for step in build_steps)
    assert any("actions/upload-artifact" in step.get("uses", "") for step in build_steps)

    publish_pypi_steps = jobs["publish-pypi"]["steps"]
    assert any("actions/download-artifact" in step.get("uses", "") for step in publish_pypi_steps)
    assert any(
        "gh-action-pypi-publish" in step.get("uses", "")
        for step in publish_pypi_steps
    )
