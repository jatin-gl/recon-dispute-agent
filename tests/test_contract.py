"""Tests that the agent parses the engine's v1.0 data contract faithfully."""

from recon_agent import load_report
from recon_agent.models import DiscrepancyType, Money, Report, Severity


def test_load_example_report(report: Report):
    assert report.contract_version == "1.0"
    assert report.currency == "USD"
    assert report.summary.discrepancy_count == len(report.discrepancies) == 6


def test_money_is_integer_minor_units(report: Report):
    total = report.summary.total_monetary_impact
    assert isinstance(total.amount_minor, int)
    assert total.amount_minor == 21650
    assert total.as_decimal() == "216.50 USD"


def test_discrepancy_enums_parsed(report: Report):
    types = {d.type for d in report.discrepancies}
    assert DiscrepancyType.DUPLICATE_IN_PSP in types
    assert DiscrepancyType.AMOUNT_MISMATCH in types
    for d in report.discrepancies:
        assert isinstance(d.severity, Severity)


def test_records_carry_typed_money(report: Report):
    for d in report.discrepancies:
        for rec in (d.psp_record, d.ledger_record):
            if rec is not None:
                assert isinstance(rec.amount, Money)
                assert rec.amount.currency == "USD"


def test_load_report_from_json_string():
    payload = (
        '{"report_id":"r1","contract_version":"1.0","generated_at":"2026-01-16T00:00:00Z",'
        '"currency":"USD","summary":{"psp_count":0,"ledger_count":0,"matched_count":0,'
        '"discrepancy_count":0,"total_monetary_impact":{"amount_minor":0,"currency":"USD"}},'
        '"discrepancies":[]}'
    )
    r = load_report(payload)
    assert r.report_id == "r1"
    assert r.discrepancies == []


def test_negative_money_decimal():
    assert Money(amount_minor=-450, currency="EUR").as_decimal() == "-4.50 EUR"
