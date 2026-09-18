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

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field, model_validator

# --------------------------------------------------------------------------- #
# Data contract (input) — see the engine's docs/data-contract.md
# --------------------------------------------------------------------------- #
# ISO-4217 currencies whose minor-unit exponent is not the default of 2.
_MINOR_UNIT_DIGITS = {
    # Zero-decimal (the amount is already in whole units).
    "JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "XOF": 0, "XAF": 0,
    "XPF": 0, "BIF": 0, "DJF": 0, "GNF": 0, "KMF": 0, "PYG": 0, "RWF": 0,
    "UGX": 0, "VUV": 0,
    # Three-decimal.
    "BHD": 3, "KWD": 3, "OMR": 3, "TND": 3, "JOD": 3, "IQD": 3, "LYD": 3,
}


class Money(BaseModel):
    amount_minor: int
    currency: str

    def as_decimal(self) -> str:
        """Human-readable rendering using the currency's minor-unit exponent.

        e.g. 21650 USD -> '216.50 USD', 100 JPY -> '100 JPY', 1234 BHD ->
        '1.234 BHD'. Display only — never for settlement math.
        """
        exp = _MINOR_UNIT_DIGITS.get(self.currency.upper(), 2)
        sign = "-" if self.amount_minor < 0 else ""
        v = abs(self.amount_minor)
        if exp == 0:
            return f"{sign}{v} {self.currency}"
        div = 10**exp
        return f"{sign}{v // div}.{v % div:0{exp}d} {self.currency}"


class DiscrepancyType(StrEnum):
    MISSING_IN_LEDGER = "MISSING_IN_LEDGER"
    MISSING_IN_PSP = "MISSING_IN_PSP"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    FEE_MISMATCH = "FEE_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    DUPLICATE_IN_PSP = "DUPLICATE_IN_PSP"
    DUPLICATE_IN_LEDGER = "DUPLICATE_IN_LEDGER"
    # Forward-compatibility sentinel: a type this version of the agent does not
    # recognize (e.g. a new type added by a newer engine) is mapped here so the
    # report still parses and the item is escalated rather than dropped.
    OTHER = "OTHER"


class Severity(StrEnum):
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
    # Preserves the original type string when it was an unrecognized value coerced
    # to OTHER, so a human still sees what the engine actually reported.
    raw_type: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _tolerate_unknown_contract_values(cls, data: object) -> object:
        """Keep an evolving contract from failing the whole report.

        An unknown discrepancy ``type`` is mapped to OTHER (with the original
        preserved in ``raw_type``) and an unknown ``severity`` is treated as HIGH.
        Both force the item toward escalation instead of rejecting the entire
        report because of one value this version doesn't know.
        """
        if not isinstance(data, dict):
            return data
        known_types = {e.value for e in DiscrepancyType}
        t = data.get("type")
        if not (isinstance(t, str) and t in known_types):
            # Unknown, missing, null, or non-string type -> OTHER, preserving the
            # original string if there was one. Never drop the whole report.
            data = {**data, "type": DiscrepancyType.OTHER.value,
                    "raw_type": t if isinstance(t, str) else None}
        known_sev = {e.value for e in Severity}
        s = data.get("severity")
        if not (isinstance(s, str) and s in known_sev):
            data = {**data, "severity": Severity.HIGH.value}
        return data

    @property
    def type_label(self) -> str:
        """The original type string when known, else the recognized enum value."""
        return self.raw_type or self.type.value


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
class RootCause(StrEnum):
    """The taxonomy of reconciliation root causes the agent classifies into."""

    TIMING_LAG = "TIMING_LAG"  # settlement recorded on one side; other side lags
    DUPLICATE_WEBHOOK = "DUPLICATE_WEBHOOK"  # retried webhook double-booked a row
    FEE_SCHEDULE_DRIFT = "FEE_SCHEDULE_DRIFT"  # ledger used a stale fee schedule
    ROUNDING_OR_FX = "ROUNDING_OR_FX"  # FX/rounding drift between auth and settle
    UNRECORDED_REFUND = "UNRECORDED_REFUND"  # refund booked one side, not the other
    FAILED_REFUND = "FAILED_REFUND"  # refund attempted at PSP but failed
    MISSING_CAPTURE = "MISSING_CAPTURE"  # ledger booked revenue never captured at PSP
    UNKNOWN = "UNKNOWN"  # evidence insufficient to classify


class RecommendedAction(StrEnum):
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

    # Serialized into JSON / the FastAPI response, not just the CLI text view.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def escalated_count(self) -> int:
        return sum(1 for i in self.investigations if i.escalated)
