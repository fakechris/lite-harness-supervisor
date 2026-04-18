"""Review-source adapter contract and v1 implementation.

v1 ships a single adapter: ``external_review.ExternalReviewSource`` — the
supervisor-issued external-review return path. A GitHub adapter (Task 5b)
will be added against the same base contract without base-class changes.

Source drivers must never touch run state or call terminal.inject() —
they only emit normalized ``ResultDelivery`` records that the daemon
feeds into the event-plane ingest (Task 3).
"""
