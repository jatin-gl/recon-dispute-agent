"""Command-line entrypoint: investigate an engine report and print the findings.

    recon-agent report.json                       # offline, text summary
    recon-agent report.json --mode anthropic       # use Claude (needs API key)
    recon-agent report.json --format json --out investigation.json
    reconcile --format json | recon-agent -         # read the report from stdin
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

from pydantic import ValidationError

from .models import Investigation, InvestigationReport, Report
from .runtime import build_agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon-agent", description=__doc__)
    parser.add_argument("report", help="path to an engine report JSON file, or '-' to read from stdin")
    parser.add_argument("--mode", choices=["offline", "anthropic"], default="offline",
                        help="agent brain: deterministic offline (default) or Claude-backed")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--out", help="write output here instead of stdout")
    args = parser.parse_args(argv)

    # The report is always JSON here (a file we read, or stdin) — parse it
    # directly rather than routing already-read content through a path heuristic,
    # which would misread BOM-prefixed content as a filename.
    try:
        raw = sys.stdin.read() if args.report == "-" else Path(args.report).read_text()
    except OSError as exc:
        print(f"error: cannot read report: {exc}", file=sys.stderr)
        return 1
    raw = raw.lstrip("\ufeff")  # tolerate a UTF-8 BOM
    try:
        report = Report.model_validate_json(raw)
    except ValidationError as exc:
        print(f"error: invalid report ({exc.error_count()} validation error(s))", file=sys.stderr)
        return 1

    agent = build_agent(args.mode)
    result = agent.run(report)

    rendered = result.model_dump_json(indent=2) if args.format == "json" else _text(result)
    if args.out:
        Path(args.out).write_text(
            rendered + ("\n" if not rendered.endswith("\n") else ""), encoding="utf-8"
        )
    else:
        _print_utf8(rendered)
    return 0


def _print_utf8(text: str) -> None:
    """Print without crashing on a non-UTF-8 stdout (the ⚠/✓ glyphs)."""
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))


def _text(result: InvestigationReport) -> str:
    lines = [
        f"Investigation of report {result.source_report_id} (contract v{result.contract_version})",
        f"  {len(result.investigations)} discrepancies investigated, "
        f"{result.escalated_count} escalated to human review",
        "",
    ]
    for inv in result.investigations:
        lines.append(_render(inv))
    return "\n".join(lines)


def _render(inv: Investigation) -> str:
    flag = "⚠ ESCALATED" if inv.escalated else "✓ auto"
    r = inv.resolution
    v = inv.verification
    return (
        f"[{inv.severity:<8}] {inv.discrepancy_type:<18} {inv.match_key}\n"
        f"    root cause : {r.root_cause.value} (confidence {r.confidence:.2f})\n"
        f"    action     : {r.recommended_action.value}  [{flag}]\n"
        f"    rationale  : {r.rationale}\n"
        f"    verified   : {v.approved} — {v.reason}\n"
        f"    evidence   : {len(inv.evidence)} tool call(s) over {inv.steps} step(s)"
    )


if __name__ == "__main__":
    sys.exit(main())
