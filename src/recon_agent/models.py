"""Domain models.

Two families live here:

* The **data contract** types (`Money`, `Transaction`, `Discrepancy`, `Summary`,
  `Report`) mirror the JSON emitted by the ``payment-reconciliation-engine``
  (contract v1.0). They are the input to the agent.
* The **agent output** types (`RootCause`, `RecommendedAction`, `Resolution`,
  `VerificationResult`, `Investigation`) are what the agent produces.

Money is always integer minor units plus an ISO-4217 currency — the agent never
does floating-point arithmetic on amounts, matching the engine's guarantee.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Data contract (input) — see the engine's docs/data-contract.md
# --------------------------------------------------------------------------- #
class Money(BaseModel):
    amount_minor: int
    currency: str

    def as_decimal(self) -> str:
        """Human-readable rendering, e.g. 21650 -> '216.50'. Display only."""
        sign = "-" if self.amount_minor < 0 else ""
        v = abs(self.amount_minor)
        return f"{sign}{v // 100}.{v % 100:02d} {self.currency}"


class DiscrepancyType(str, Enum):
    MISSING_IN_LEDGER = "MISSING_IN_LEDGER"
    MISSING_IN_PSP = "MISSING_IN_PSP"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    FEE_MISMATCH = "FEE_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    DUPLICATE_IN_PSP = "DUPLICATE_IN_PSP"
    DUPLICATE_IN_LEDGER = "DUPLICATE_IN_LEDGER"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Transaction(BaseModel):
    match_key: str
    external_id: str
    source: str
    amount: Money
    fee: Money
    status: str
    timestamp: str | None = None


class Discrepancy(BaseModel):
    id: str
    type: DiscrepancyType
    severity: Severity
    match_key: str
    psp_record: Transaction | None = None
    ledger_record: Transaction | None = None
    monetary_impact: Money
    detail: str


class Summary(BaseModel):
    psp_count: int
    ledger_count: int
    matched_count: int
    discrepancy_count: int
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    total_monetary_impact: Money


class Report(BaseModel):
    report_id: str
    contract_version: str
    generated_at: str
    currency: str
    summary: Summary
    discrepancies: list[Discrepancy]


# --------------------------------------------------------------------------- #
# Agent output
# --------------------------------------------------------------------------- #
class RootCause(str, Enum):
    """The taxonomy of reconciliation root causes the agent classifies into."""

    TIMING_LAG = "TIMING_LAG"  # settlement recorded on one side; other side lags
    DUPLICATE_WEBHOOK = "DUPLICATE_WEBHOOK"  # retried webhook double-booked a row
    FEE_SCHEDULE_DRIFT = "FEE_SCHEDULE_DRIFT"  # ledger used a stale fee schedule
    ROUNDING_OR_FX = "ROUNDING_OR_FX"  # FX/rounding drift between auth and settle
    UNRECORDED_REFUND = "UNRECORDED_REFUND"  # refund booked one side, not the other
    FAILED_REFUND = "FAILED_REFUND"  # refund attempted at PSP but failed
    MISSING_CAPTURE = "MISSING_CAPTURE"  # ledger booked revenue never captured at PSP
    UNKNOWN = "UNKNOWN"  # evidence insufficient to classify


class RecommendedAction(str, Enum):
    NO_ACTION_TIMING = "NO_ACTION_TIMING"  # self-resolves next cycle; monitor
    DEDUPE_LEDGER_ENTRY = "DEDUPE_LEDGER_ENTRY"
    UPDATE_FEE_RECORD = "UPDATE_FEE_RECORD"
    ADJUST_LEDGER_AMOUNT = "ADJUST_LEDGER_AMOUNT"
    REVERSE_LEDGER_ENTRY = "REVERSE_LEDGER_ENTRY"
    REISSUE_REFUND = "REISSUE_REFUND"
    MANUAL_REVIEW = "MANUAL_REVIEW"  # ambiguous / low confidence — human required


class Resolution(BaseModel):
    root_cause: RootCause
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: RecommendedAction
    rationale: str


class VerificationResult(BaseModel):
    approved: bool
    reason: str


class Investigation(BaseModel):
    """The complete record of investigating one discrepancy."""

    discrepancy_id: str
    match_key: str
    discrepancy_type: str
    severity: str
    resolution: Resolution
    verification: VerificationResult
    evidence: list[str] = Field(default_factory=list)
    escalated: bool = False
    steps: int = 0


class InvestigationReport(BaseModel):
    """The agent's output over a whole reconciliation report."""

    source_report_id: str
    contract_version: str
    investigations: list[Investigation]

    @property
    def escalated_count(self) -> int:
        return sum(1 for i in self.investigations if i.escalated)
