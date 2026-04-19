"""Inbound boundary guard — transport-agnostic ingress safety layer.

Components (each independently toggleable via ``InboundGuardConfig``):

- auth        — bearer-token check; localhost fallback when token unset
- rate_limit  — sliding-window per client_id, thread-safe
- injection   — pattern scan on inbound text
- redaction   — regex scrub for API keys / tokens / JWT on outbound text
- audit       — append-only JSONL via the event-plane atomic-append helper

``InboundGuard.check(InboundRequest) -> GuardResult`` is the public entry
point. Consumers today: the A2A adapter (``supervisor/adapters/a2a``).
Future consumers (webhooks, HTTP CLI) share the same chain.
"""
from .models import GuardResult, InboundGuardConfig, InboundRequest

__all__ = ["GuardResult", "InboundGuardConfig", "InboundRequest"]
