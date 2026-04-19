"""``InboundGuard`` — the facade that chains boundary components.

Order is fixed and load-bearing:

1. **auth** — reject unauthenticated callers before spending any more
   CPU on them.
2. **rate_limit** — reject floods before running regex-heavy scanners.
3. **injection** — reject obvious payload-shape attacks before we write
   anything observable.
4. **redaction** — scrub the accepted payload so downstream writes never
   persist raw API keys / JWTs.
5. **audit** — always write one JSONL line (pass or fail) for
   traceability.

Each stage is individually toggleable via the config. When a stage is
disabled, it is skipped cleanly; the chain still produces a correct
``GuardResult``. A failed stage short-circuits the chain — but audit
still runs so the failure is recorded.
"""
from __future__ import annotations

from .audit import append_audit, make_audit_record
from .auth import check_auth
from .injection import scan as scan_injection
from .models import GuardResult, InboundGuardConfig, InboundRequest
from .rate_limit import RateLimiter
from .redaction import redact


class InboundGuard:
    def __init__(self, config: InboundGuardConfig):
        self._config = config
        self._rate_limiter = RateLimiter(config.rate_limit_per_minute) if config.enable_rate_limit else None

    def check(self, req: InboundRequest) -> GuardResult:
        result = self._run_chain(req)
        if self._config.enable_audit and self._config.audit_path is not None:
            try:
                append_audit(self._config.audit_path, make_audit_record(req, result))
            except OSError:
                # Audit failure must never block the chain decision.
                pass
        return result

    def _run_chain(self, req: InboundRequest) -> GuardResult:
        if self._config.enable_auth:
            res = check_auth(req, self._config)
            if not res.ok:
                return res

        if self._rate_limiter is not None:
            if not self._rate_limiter.check(req.client_id):
                return GuardResult(
                    ok=False, stage="rate_limit", reason="per-client limit exceeded", normalized_text=""
                )

        text = req.text
        if self._config.enable_injection_scan:
            res = scan_injection(text)
            if not res.ok:
                return res

        if self._config.enable_redaction:
            text = redact(text, redact_emails=self._config.redact_emails)

        return GuardResult(ok=True, stage="", reason="", normalized_text=text)
