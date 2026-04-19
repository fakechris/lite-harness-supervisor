"""Tests for boundary auth.

Rules:
- No token configured → accept iff client_id is localhost (127.0.0.1, ::1,
  or a UNIX-domain peer represented as empty / "local"). Otherwise reject.
- Token configured → require ``Authorization: Bearer <token>`` exact
  match (constant-time). Any mismatch / missing header → reject.
- ``Bearer`` prefix is case-insensitive (matches common HTTP practice).
"""
from __future__ import annotations

from supervisor.boundary.auth import check_auth
from supervisor.boundary.models import InboundGuardConfig, InboundRequest


def _req(client_id: str = "127.0.0.1", headers: dict | None = None) -> InboundRequest:
    return InboundRequest(
        client_id=client_id,
        text="hi",
        transport="a2a",
        headers=headers or {},
    )


def test_accepts_localhost_when_no_token_configured():
    cfg = InboundGuardConfig(auth_token="")
    for cid in ("127.0.0.1", "::1", "localhost", "local"):
        assert check_auth(_req(client_id=cid), cfg).ok, cid


def test_rejects_non_localhost_when_no_token_configured():
    cfg = InboundGuardConfig(auth_token="")
    res = check_auth(_req(client_id="10.0.0.1"), cfg)
    assert not res.ok
    assert res.stage == "auth"


def test_accepts_valid_bearer_token():
    cfg = InboundGuardConfig(auth_token="s3cret")
    res = check_auth(
        _req(client_id="10.0.0.1", headers={"Authorization": "Bearer s3cret"}),
        cfg,
    )
    assert res.ok


def test_bearer_prefix_is_case_insensitive():
    cfg = InboundGuardConfig(auth_token="s3cret")
    res = check_auth(
        _req(headers={"Authorization": "bearer s3cret"}),
        cfg,
    )
    assert res.ok


def test_rejects_wrong_token():
    cfg = InboundGuardConfig(auth_token="s3cret")
    res = check_auth(
        _req(headers={"Authorization": "Bearer wrong"}),
        cfg,
    )
    assert not res.ok
    assert res.stage == "auth"


def test_rejects_missing_authorization_header_when_token_required():
    cfg = InboundGuardConfig(auth_token="s3cret")
    res = check_auth(_req(client_id="127.0.0.1"), cfg)
    # Localhost does NOT bypass when token is configured — explicit is better
    assert not res.ok
    assert res.stage == "auth"


def test_accepts_header_lookup_case_insensitive():
    cfg = InboundGuardConfig(auth_token="t")
    # Real HTTP libraries often lowercase header names.
    res = check_auth(_req(headers={"authorization": "Bearer t"}), cfg)
    assert res.ok
