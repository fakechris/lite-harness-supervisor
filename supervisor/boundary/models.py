"""Data model for the inbound boundary guard.

All three types are plain dataclasses — ``InboundRequest`` / ``GuardResult``
are frozen because callers pass them through a chain and mutation would be
a bug; ``InboundGuardConfig`` is deliberately mutable so tests can tweak
fields before handing it to ``InboundGuard``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class InboundRequest:
    client_id: str
    text: str
    transport: str
    headers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    stage: str
    reason: str
    normalized_text: str


@dataclass
class InboundGuardConfig:
    enable_auth: bool = True
    enable_rate_limit: bool = True
    enable_injection_scan: bool = True
    enable_redaction: bool = True
    enable_audit: bool = True

    auth_token: str = ""
    rate_limit_per_minute: int = 20
    redact_emails: bool = False
    audit_path: Optional[Path] = None
