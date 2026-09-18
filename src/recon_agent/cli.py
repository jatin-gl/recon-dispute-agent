"""Command-line entrypoint: investigate an engine report and print the findings.

    recon-agent report.json                       # offline, text summary
    recon-agent report.json --mode anthropic       # use Claude (needs API key)
    recon-agent report.json --format json --out investigation.json
    reconcile --format json | recon-agent -         # read the report from stdin
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import load_report
from .models import Investigation, InvestigationReport
from .runtime import build_agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon-agent", description=__doc__)
    parser.add_argument("report", help="path to an engine report JSON file, or '-' to read from stdin")
    parser.add_argument("--mode", choices=["offline", "anthropic"], default="offline",
                        help="agent brain: deterministic offline (default) or Claude-backed")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--out", help="write output here instead of stdout")
    args = parser.parse_args(argv)

    report = load_report(sys.stdin.read()) if args.report == "-" else load_report(Path(args.report))
    agent = build_agent(args.mode)
    result = agent.run(report)

    rendered = result.model_dump_json(indent=2) if args.format == "json" else _text(result)
    if args.out:
        Path(args.out).write_text(rendered + ("\n" if not rendered.endswith("\n") else ""))
    else:
        print(rendered)
    return 0


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
