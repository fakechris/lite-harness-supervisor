"""Inbound-audit append-only JSONL.

Every inbound request (pass or fail) gets one line. We hash the raw text
with SHA-256 rather than storing it — audit is for traceability, not
replay. Raw text lives (or doesn't) in the mailbox / result payload
under the usual access rules.

Path convention: ``.supervisor/runtime/shared/inbound_audit.jsonl`` for
the production runtime; tests pass an explicit path.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from supervisor.storage.state_store import _atomic_append_line

from .models import GuardResult, InboundRequest


def make_audit_record(req: InboundRequest, result: GuardResult) -> dict:
    text_bytes = (req.text or "").encode("utf-8")
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "transport": req.transport,
        "client_id": req.client_id,
        "ok": result.ok,
        "stage": result.stage,
        "reason": result.reason,
        "text_hash": hashlib.sha256(text_bytes).hexdigest(),
    }


def append_audit(path: Path, record: dict) -> None:
    _atomic_append_line(path, json.dumps(record, ensure_ascii=False))
