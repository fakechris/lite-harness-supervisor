"""Session-first event plane for deferred external work.

See docs/plans/2026-04-17-session-first-async-review-event-plane-prd.md.

Layered responsibilities:
- models: ExternalTaskRequest, ExternalTaskResult, SessionWait, SessionMailboxItem.
- store: append-only durable substrate under .supervisor/runtime/shared/.
- ingest (Task 3): daemon-owned request/result ingestion and correlation.
- wake_policy (Task 4): notify | wake | defer | record_only decisions.
"""
