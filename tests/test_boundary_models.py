"""Tests for the boundary-guard data model.

Public contract:

- ``InboundRequest`` carries enough context for every component downstream
  (auth, rate_limit, injection, redaction, audit) without having to know
  about HTTP specifics. Transport name is a free-form string so future
  callers (webhook, cli) reuse the same chain.
- ``GuardResult`` names *which stage* made the call and *why*. Callers
  can route failures based on ``stage`` without string-matching reasons.
- ``InboundGuardConfig`` is frozen-ish (dataclass, not strictly frozen so
  tests can mutate before passing in) and every component toggle defaults
  to on; internal callers turn things off explicitly.
"""
from __future__ import annotations

from pathlib import Path

from supervisor.boundary.models import (
    GuardResult,
    InboundGuardConfig,
    InboundRequest,
)


def test_inbound_request_minimal_fields():
    req = InboundRequest(client_id="127.0.0.1", text="hello", transport="a2a")
    assert req.client_id == "127.0.0.1"
    assert req.text == "hello"
    assert req.transport == "a2a"
    assert req.headers == {}


def test_inbound_request_accepts_headers():
    req = InboundRequest(
        client_id="10.0.0.1",
        text="x",
        transport="http",
        headers={"Authorization": "Bearer abc"},
    )
    assert req.headers["Authorization"] == "Bearer abc"


def test_guard_result_pass_and_fail():
    ok = GuardResult(ok=True, stage="", reason="", normalized_text="hello")
    fail = GuardResult(ok=False, stage="auth", reason="missing token", normalized_text="")
    assert ok.ok is True
    assert fail.ok is False
    assert fail.stage == "auth"
    assert fail.reason == "missing token"


def test_guard_config_defaults_enable_all_components():
    cfg = InboundGuardConfig()
    assert cfg.enable_auth is True
    assert cfg.enable_rate_limit is True
    assert cfg.enable_injection_scan is True
    assert cfg.enable_redaction is True
    assert cfg.enable_audit is True
    assert cfg.rate_limit_per_minute == 20
    assert cfg.auth_token == ""
    assert cfg.audit_path is None


def test_guard_config_toggles_are_independent():
    cfg = InboundGuardConfig(enable_auth=False, enable_rate_limit=False)
    assert cfg.enable_auth is False
    assert cfg.enable_rate_limit is False
    # untouched defaults still on
    assert cfg.enable_injection_scan is True
    assert cfg.enable_redaction is True


def test_guard_config_audit_path_accepts_pathlib(tmp_path: Path):
    audit_path = tmp_path / "inbound_audit.jsonl"
    cfg = InboundGuardConfig(audit_path=audit_path)
    assert cfg.audit_path == audit_path
