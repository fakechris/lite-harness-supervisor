# Supervisor Oracle Capability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a lightweight, auditable oracle consultation layer to thin-supervisor and document how it complements the existing supervisor and acceptance-gate layers.

**Architecture:** Introduce a first-class `OracleOpinion` object plus a small `supervisor.oracle` client that can call an external reasoning provider or fall back to self-adversarial review when no API key is configured. Expose it through a new `thin-supervisor oracle consult` CLI and allow optional persistence into the existing collaboration plane as a shared note.

**Tech Stack:** Python 3.10 stdlib (`urllib`, `json`, `dataclasses`), existing CLI in `supervisor/app.py`, existing collaboration plane via `DaemonClient.note_add`, pytest.

### Task 1: Add the first-class oracle object

**Files:**
- Modify: `supervisor/domain/models.py`
- Test: `tests/test_oracle_client.py`

**Step 1: Write the failing test**

```python
from supervisor.domain.models import OracleOpinion

def test_oracle_opinion_auto_ids_and_serializes():
    opinion = OracleOpinion(
        provider="openai",
        model_name="o3",
        mode="review",
        question="What is wrong here?",
        files=["a.py"],
        response_text="Independent analysis",
    )
    assert opinion.consultation_id.startswith("oracle_")
    assert opinion.to_dict()["provider"] == "openai"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_oracle_client.py::test_oracle_opinion_auto_ids_and_serializes -q`
Expected: FAIL because `OracleOpinion` does not exist yet

**Step 3: Write minimal implementation**

Add a dataclass with generated ID + timestamp and `to_dict()`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_oracle_client.py::test_oracle_opinion_auto_ids_and_serializes -q`
Expected: PASS

### Task 2: Implement oracle provider detection and fallback consultation

**Files:**
- Create: `supervisor/oracle/client.py`
- Test: `tests/test_oracle_client.py`

**Step 1: Write the failing tests**

```python
def test_detect_provider_prefers_openai(monkeypatch):
    ...

def test_consult_without_api_key_returns_self_review(monkeypatch):
    ...
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_oracle_client.py -q`
Expected: FAIL because the oracle client does not exist yet

**Step 3: Write minimal implementation**

Implement:
- provider detection (`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`)
- focused file loading for prompt context
- external-call scaffolding
- deterministic self-review fallback when no key is available

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_oracle_client.py -q`
Expected: PASS

### Task 3: Expose the feature through the CLI

**Files:**
- Modify: `supervisor/app.py`
- Modify: `supervisor/daemon/client.py`
- Test: `tests/test_app_cli.py`

**Step 1: Write the failing tests**

```python
def test_oracle_consult_json_output(...):
    ...

def test_oracle_consult_saves_note_for_run(...):
    ...
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app_cli.py -q`
Expected: FAIL because `oracle consult` command is not wired

**Step 3: Write minimal implementation**

Add:
- `thin-supervisor oracle consult`
- `--question`, `--file`, `--mode`, `--provider`, `--run`, `--json`
- optional note persistence through the collaboration plane

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app_cli.py -q`
Expected: PASS

### Task 4: Document the Amp-vs-supervisor review and the new oracle layer

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Create: `docs/reviews/2026-04-12-amp-supervisor-capability-review.md`

**Step 1: Write the documentation**

Document:
- current capability matrix vs Amp (`oracle`, `supervisor`, `acceptance gate`)
- what is implemented today
- what the new oracle consult capability adds
- what remains intentionally out of scope (sub-agent platform, full thread handoff)

**Step 2: Verify links and consistency**

Run: a small local link check over the touched docs
Expected: all referenced local paths exist

### Task 5: Final verification

**Files:**
- Verify only

**Step 1: Run targeted tests**

Run: `pytest tests/test_oracle_client.py tests/test_app_cli.py -q`
Expected: PASS

**Step 2: Run full suite**

Run: `pytest -q`
Expected: PASS

**Step 3: Review diff**

Run: `git diff --stat`
Expected: only oracle capability + documentation changes
