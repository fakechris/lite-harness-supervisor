import json

from supervisor.eval.reporting import default_report_dir, save_eval_report


def test_default_report_dir_uses_supervisor_evals_reports():
    path = default_report_dir(".supervisor/runtime")

    assert str(path) == ".supervisor/evals/reports"


def test_save_eval_report_writes_default_location(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    payload = {"suite": "approval-core", "counts": {"total": 4}}
    path = save_eval_report(payload, report_kind="run", runtime_dir=".supervisor/runtime")

    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["report_kind"] == "run"
    assert saved["payload"]["suite"] == "approval-core"


def test_save_eval_report_uses_unique_default_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = {"suite": "approval-core"}

    first = save_eval_report(payload, report_kind="run", runtime_dir=".supervisor/runtime")
    second = save_eval_report(payload, report_kind="run", runtime_dir=".supervisor/runtime")

    assert first != second
