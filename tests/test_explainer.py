"""Tests for the explainer client (stub mode) and async job tracker."""

import time
from supervisor.llm.explainer_client import ExplainerClient
from supervisor.operator.jobs import JobTracker, Job


# ── ExplainerClient stub mode tests ────────────────────────────────

def _make_context(**overrides):
    base = {
        "run_state": {
            "run_id": "run_abc",
            "top_state": "RUNNING",
            "current_node_id": "step_1",
            "done_node_ids": ["step_0"],
            "current_attempt": 1,
            "last_agent_checkpoint": {"summary": "writing unit tests"},
            "last_decision": {"next_instruction": "continue with tests"},
            "retry_budget": {"per_node": 3, "global_limit": 12, "used_global": 0},
            "node_mismatch_count": 0,
            "auto_intervention_count": 0,
        },
        "recent_events": [],
        "language": "en",
    }
    base.update(overrides)
    return base


class TestExplainRunStub:
    def test_returns_structured_result(self):
        client = ExplainerClient(model=None)
        result = client.explain_run(_make_context())

        assert "explanation" in result
        assert "current_activity" in result
        assert "recent_progress" in result
        assert "next_expected" in result
        assert result["confidence"] == 0.3

    def test_includes_checkpoint_summary(self):
        client = ExplainerClient(model=None)
        result = client.explain_run(_make_context())

        assert "writing unit tests" in result["explanation"]
        assert "step_1" in result["current_activity"]

    def test_shows_done_nodes(self):
        client = ExplainerClient(model=None)
        result = client.explain_run(_make_context())

        assert "step_0" in result["recent_progress"]

    def test_empty_state(self):
        client = ExplainerClient(model=None)
        result = client.explain_run({"run_state": {}, "language": "en"})

        assert "UNKNOWN" in result["explanation"]
        assert result["confidence"] == 0.3


class TestExplainExchangeStub:
    def test_returns_structured_result(self):
        client = ExplainerClient(model=None)
        ctx = _make_context(exchange={
            "last_checkpoint_summary": "tests passing",
            "last_instruction_summary": "move to step_2",
        })
        result = client.explain_exchange(ctx)

        assert "explanation" in result
        assert "worker_intent" in result
        assert "supervisor_response" in result
        assert "tests passing" in result["worker_intent"]
        assert "move to step_2" in result["supervisor_response"]

    def test_empty_exchange(self):
        client = ExplainerClient(model=None)
        ctx = _make_context(exchange={})
        result = client.explain_exchange(ctx)

        assert "no checkpoint" in result["worker_intent"]


class TestAssessDriftStub:
    def test_on_track(self):
        client = ExplainerClient(model=None)
        result = client.assess_drift(_make_context())

        assert result["status"] == "on_track"
        assert result["recommended_action"] == "No action needed"

    def test_watch_with_retries(self):
        client = ExplainerClient(model=None)
        ctx = _make_context()
        ctx["run_state"]["retry_budget"]["used_global"] = 4
        result = client.assess_drift(ctx)

        assert result["status"] == "watch"
        assert any("retry" in r.lower() for r in result["reasons"])

    def test_drifting_with_multiple_signals(self):
        client = ExplainerClient(model=None)
        ctx = _make_context()
        ctx["run_state"]["retry_budget"]["used_global"] = 6
        ctx["run_state"]["node_mismatch_count"] = 3
        ctx["run_state"]["auto_intervention_count"] = 2
        result = client.assess_drift(ctx)

        assert result["status"] == "drifting"
        assert len(result["reasons"]) >= 2

    def test_evidence_fields(self):
        client = ExplainerClient(model=None)
        result = client.assess_drift(_make_context())

        assert any("retries_used=" in e for e in result["evidence"])
        assert any("node_mismatches=" in e for e in result["evidence"])


# ── Chinese stub mode tests ──────────────────────────────────────

class TestStubChineseMode:
    def test_explain_run_zh(self):
        client = ExplainerClient(model=None)
        ctx = _make_context(language="zh")
        result = client.explain_run(ctx)
        # Should contain Chinese text
        assert "状态" in result["current_activity"] or "节点" in result["current_activity"]
        assert "confidence" in result

    def test_explain_exchange_zh(self):
        client = ExplainerClient(model=None)
        ctx = _make_context(language="zh")
        ctx["exchange"] = {
            "last_checkpoint_summary": "writing tests",
            "last_instruction_summary": "continue",
        }
        result = client.explain_exchange(ctx)
        assert "检查点" in result["explanation"] or "Worker" in result["explanation"]

    def test_assess_drift_zh(self):
        client = ExplainerClient(model=None)
        ctx = _make_context(language="zh")
        result = client.assess_drift(ctx)
        assert result["status"] == "on_track"
        assert any("未检测" in r for r in result["reasons"])

    def test_drift_watch_zh(self):
        client = ExplainerClient(model=None)
        ctx = _make_context(language="zh")
        ctx["run_state"]["retry_budget"]["used_global"] = 5
        result = client.assess_drift(ctx)
        assert result["status"] == "watch"
        assert "继续观察" in result["recommended_action"]


# ── JobTracker tests ───────────────────────────────────────────────

class TestJobTracker:
    def test_submit_and_get(self):
        tracker = JobTracker()
        job_id = tracker.submit("test", lambda: {"result": "ok"})

        assert job_id.startswith("job_")
        # Wait for completion
        for _ in range(50):
            job = tracker.get(job_id)
            if job and job.status == "completed":
                break
            time.sleep(0.01)

        job = tracker.get(job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.result == {"result": "ok"}

    def test_failed_job(self):
        tracker = JobTracker()

        def _fail():
            raise ValueError("boom")

        job_id = tracker.submit("fail_test", _fail)
        for _ in range(50):
            job = tracker.get(job_id)
            if job and job.status == "failed":
                break
            time.sleep(0.01)

        job = tracker.get(job_id)
        assert job is not None
        assert job.status == "failed"
        assert "boom" in job.error

    def test_get_nonexistent(self):
        tracker = JobTracker()
        assert tracker.get("job_nonexistent") is None

    def test_list_jobs(self):
        tracker = JobTracker()
        tracker.submit("a", lambda: {})
        tracker.submit("b", lambda: {})
        time.sleep(0.1)

        jobs = tracker.list_jobs()
        assert len(jobs) == 2

    def test_list_jobs_filter_by_kind(self):
        tracker = JobTracker()
        tracker.submit("explain", lambda: {})
        tracker.submit("drift", lambda: {})
        time.sleep(0.1)

        jobs = tracker.list_jobs(kind="explain")
        assert len(jobs) == 1
        assert jobs[0].kind == "explain"

    def test_eviction(self):
        tracker = JobTracker(max_completed=2)
        ids = []
        for i in range(5):
            ids.append(tracker.submit("test", lambda i=i: {"i": i}))
        time.sleep(0.2)

        # Old completed jobs should be evicted
        remaining = tracker.list_jobs()
        assert len(remaining) <= 4  # at most max_completed + running

    def test_job_to_dict(self):
        job = Job(
            job_id="job_test",
            kind="explain_run",
            status="completed",
            result={"explanation": "ok"},
            created_at="2026-04-15T10:00:00Z",
            completed_at="2026-04-15T10:00:01Z",
        )
        d = job.to_dict()
        assert d["job_id"] == "job_test"
        assert d["kind"] == "explain_run"
        assert d["status"] == "completed"
        assert d["result"]["explanation"] == "ok"
