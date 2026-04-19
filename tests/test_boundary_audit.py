"""Tests for the boundary audit log.

Contract:
- ``append_audit(path, record)`` appends one JSONL line via the project's
  fcntl-locked ``_atomic_append_line`` helper.
- Record carries ``ts`` (ISO-8601 UTC), ``transport``, ``client_id``,
  ``stage``, ``ok``, ``reason``, ``text_hash`` (SHA-256 of the raw text).
- We never persist raw inbound text — hash only. Audit is for traceability
  and forensics, not for replaying payloads.
- Concurrent writers must not interleave lines (the underlying helper
  holds an exclusive flock).
"""
from __future__ import annotations

import hashlib
import json
import threading

from supervisor.boundary.audit import append_audit, make_audit_record
from supervisor.boundary.models import GuardResult, InboundRequest


def test_make_audit_record_hashes_text_not_stores_it():
    req = InboundRequest(client_id="127.0.0.1", text="secret payload", transport="a2a")
    result = GuardResult(ok=True, stage="", reason="", normalized_text="secret payload")
    rec = make_audit_record(req, result)
    assert rec["transport"] == "a2a"
    assert rec["client_id"] == "127.0.0.1"
    assert rec["ok"] is True
    assert rec["stage"] == ""
    expected_hash = hashlib.sha256(b"secret payload").hexdigest()
    assert rec["text_hash"] == expected_hash
    assert "text" not in rec
    assert "secret" not in json.dumps(rec)


def test_make_audit_record_preserves_failure_stage_and_reason():
    req = InboundRequest(client_id="10.0.0.1", text="x", transport="a2a")
    result = GuardResult(ok=False, stage="auth", reason="missing token", normalized_text="")
    rec = make_audit_record(req, result)
    assert rec["ok"] is False
    assert rec["stage"] == "auth"
    assert rec["reason"] == "missing token"


def test_append_audit_writes_jsonl(tmp_path):
    path = tmp_path / "inbound_audit.jsonl"
    req = InboundRequest(client_id="127.0.0.1", text="hi", transport="a2a")
    result = GuardResult(ok=True, stage="", reason="", normalized_text="hi")
    append_audit(path, make_audit_record(req, result))
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["client_id"] == "127.0.0.1"


def test_append_audit_concurrent_writers_do_not_interleave(tmp_path):
    path = tmp_path / "inbound_audit.jsonl"

    def worker(i: int):
        req = InboundRequest(client_id=f"c{i}", text=f"m{i}", transport="a2a")
        result = GuardResult(ok=True, stage="", reason="", normalized_text="")
        for _ in range(10):
            append_audit(path, make_audit_record(req, result))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 50
    for line in lines:
        json.loads(line)  # every line must parse cleanly
