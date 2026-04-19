"""Bearer-token auth with localhost fallback.

Two modes:

1. ``config.auth_token`` empty → accept iff the client_id is local
   (127.0.0.1, ::1, or a UNIX-socket peer which we canonicalise to
   "local"/""). Non-local callers are rejected.
2. ``config.auth_token`` set → require ``Authorization: Bearer <token>``
   with constant-time comparison. Localhost callers do **not** bypass
   the token — if you configure auth, you mean it.
"""
from __future__ import annotations

import hmac

from .models import GuardResult, InboundGuardConfig, InboundRequest

_LOCALHOST_IDS = frozenset({"127.0.0.1", "::1", "localhost", "local", ""})


def _lookup_header(headers: dict, name: str) -> str:
    """Case-insensitive header lookup. Returns empty string when absent."""
    lowered = name.lower()
    for k, v in headers.items():
        if k.lower() == lowered:
            return v
    return ""


def check_auth(req: InboundRequest, config: InboundGuardConfig) -> GuardResult:
    if not config.auth_token:
        if req.client_id in _LOCALHOST_IDS:
            return GuardResult(ok=True, stage="", reason="", normalized_text=req.text)
        return GuardResult(
            ok=False,
            stage="auth",
            reason="no token configured and client is not localhost",
            normalized_text="",
        )

    header = _lookup_header(req.headers, "Authorization")
    if not header:
        return GuardResult(
            ok=False, stage="auth", reason="missing Authorization header", normalized_text=""
        )

    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return GuardResult(
            ok=False, stage="auth", reason="malformed Authorization header", normalized_text=""
        )

    presented = parts[1].strip()
    if not hmac.compare_digest(presented, config.auth_token):
        return GuardResult(
            ok=False, stage="auth", reason="invalid token", normalized_text=""
        )
    return GuardResult(ok=True, stage="", reason="", normalized_text=req.text)
