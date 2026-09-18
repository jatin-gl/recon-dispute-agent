"""Mock systems of record the investigation tools query.

In a real deployment these calls would hit the production ledger database, the
PSP's API, and an events/webhook store. Here they are backed by an in-memory
dataset so the agent — real or offline — can genuinely *investigate* a
discrepancy and reach a defensible conclusion, and so the whole thing runs and
tests without external systems.

The default dataset is seeded to explain the discrepancies in the engine's
committed ``example-report.json``. For match keys it doesn't know about, each
lookup returns ``{"found": False}`` and the agent reasons from the discrepancy
record alone (falling back to MANUAL_REVIEW when evidence is thin).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class KnowledgeBase:
    """An in-memory stand-in for the ledger, PSP, and events systems of record."""

    records: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get_ledger_entry(self, reference: str) -> dict[str, Any]:
        rec = self.records.get(reference)
        if not rec or "ledger" not in rec:
            return {"found": False, "reference": reference}
        return {"found": True, **rec["ledger"]}

    def get_psp_transaction(self, reference: str) -> dict[str, Any]:
        rec = self.records.get(reference)
        if not rec or "psp" not in rec:
            return {"found": False, "reference": reference}
        return {"found": True, **rec["psp"]}

    def get_events(self, reference: str) -> dict[str, Any]:
        rec = self.records.get(reference)
        if not rec:
            return {"found": False, "reference": reference, "events": []}
        return {"found": True, "reference": reference, "events": rec.get("events", [])}

    @classmethod
    def default(cls) -> "KnowledgeBase":
        """Dataset that explains the engine's example report discrepancies."""
        return cls(
            records={
                # AMOUNT_MISMATCH: FX-settled txn; rate drifted between auth and settle.
                "TXN-1002": {
                    "ledger": {"status": "settled", "amount_minor": 19900, "booked_at": "2026-01-15", "fx_rate_used": 1.00},
                    "psp": {"status": "settled", "amount_minor": 20000, "settled_at": "2026-01-15", "settlement_currency": "USD"},
                    "events": [
                        {"type": "fx_rate", "authorized_rate": 0.995, "settled_rate": 1.000,
                         "note": "cross-currency auth; settlement rate differed from authorization rate"},
                    ],
                },
                # MISSING_IN_LEDGER: PSP settled today; ledger booking job runs T+1.
                "TXN-1003": {
                    "psp": {"status": "settled", "amount_minor": 5000, "settled_at": "2026-01-15"},
                    "events": [
                        {"type": "ledger_booking_schedule", "state": "pending", "scheduled_for": "2026-01-16",
                         "note": "nightly booking job books PSP settlements on T+1; entry not yet materialized"},
                    ],
                },
                # MISSING_IN_PSP: ledger booked revenue; PSP authorization expired uncaptured.
                "TXN-1004": {
                    "ledger": {"status": "settled", "amount_minor": 7500, "booked_at": "2026-01-15"},
                    "events": [
                        {"type": "psp_auth_lifecycle", "state": "expired", "captured": False,
                         "note": "authorization expired after 7 days without capture; no funds ever moved"},
                    ],
                },
                # FEE_MISMATCH: interchange schedule bumped; ledger used the prior version.
                "TXN-1005": {
                    "ledger": {"status": "settled", "amount_minor": 8000, "fee_minor": 450, "fee_schedule_version": "2025-11"},
                    "psp": {"status": "settled", "amount_minor": 8000, "fee_minor": 500, "fee_schedule_version": "2026-01"},
                    "events": [
                        {"type": "fee_schedule", "effective": "2026-01-01", "prior_version": "2025-11",
                         "note": "interchange +0.5% effective 2026-01-01; ledger fee table not yet updated"},
                    ],
                },
                # STATUS_MISMATCH: ledger booked a refund; the PSP refund attempt failed.
                "TXN-1006": {
                    "ledger": {"status": "refunded", "amount_minor": 12000, "booked_at": "2026-01-15"},
                    "psp": {"status": "settled", "amount_minor": 12000, "settled_at": "2026-01-15"},
                    "events": [
                        {"type": "refund_attempt", "state": "failed", "reason": "card_expired",
                         "note": "refund initiated and booked in ledger, but PSP refund failed (expired card)"},
                    ],
                },
                # DUPLICATE_IN_PSP: a retried webhook double-booked the settlement.
                "TXN-1007": {
                    "psp": {"status": "settled", "amount_minor": 9000, "settled_at": "2026-01-15"},
                    "events": [
                        {"type": "webhook_delivery", "event_id": "evt_9f3a", "attempt": 1, "http_status": 504,
                         "note": "first delivery timed out"},
                        {"type": "webhook_delivery", "event_id": "evt_9f3a", "attempt": 2, "http_status": 200,
                         "note": "retry succeeded — same event_id, produced a second settlement row"},
                    ],
                },
            }
        )
