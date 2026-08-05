from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from ..output.console import print_compare_summary
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..workspace import default_report_path, load_json, utc_now


def register(subcommands) -> None:
    command = subcommands.add_parser("compare", help="Compare old/new bench or evaluation reports for regressions.")
    command.add_argument("old_report", help="Old JSON report path.")
    command.add_argument("new_report", help="New JSON report path.")
    command.add_argument("--project-root", type=Path)
    command.add_argument("--min-pass-rate-delta", type=float, default=0.0)
    command.add_argument("--max-regressed-cases", type=int, default=0)
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def _case_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if report.get("state"):
        state = report["state"]
        return {
            state.get("run_id", "single-run"): {
                "case_id": state.get("run_id", "single-run"),
                "passed": state.get("status") == "passed",
                "quality_score": (state.get("quality") or report.get("quality") or {}).get("overall_score"),
            }
        }
    return {str(case.get("case_id")): case for case in report.get("cases") or []}


def _pass_rate(report: dict[str, Any], cases: dict[str, dict[str, Any]]) -> float:
    summary = report.get("summary") or {}
    if "pass_rate" in summary:
        return float(summary["pass_rate"])
    if report.get("state"):
        state = report["state"]
        execution = state.get("execution") or {}
        return float(execution.get("pass_rate") or (1.0 if state.get("status") == "passed" else 0.0))
    if not cases:
        return 1.0
    return sum(1 for item in cases.values() if item.get("passed")) / len(cases)


def handle(args: Namespace) -> int:
    old_report = load_json(args.old_report)
    new_report = load_json(args.new_report)
    old_cases = _case_map(old_report)
    new_cases = _case_map(new_report)
    old_pass_rate = _pass_rate(old_report, old_cases)
    new_pass_rate = _pass_rate(new_report, new_cases)
    common_ids = sorted(set(old_cases) & set(new_cases))
    fixed = [case_id for case_id in common_ids if not old_cases[case_id].get("passed") and new_cases[case_id].get("passed")]
    regressed = [case_id for case_id in common_ids if old_cases[case_id].get("passed") and not new_cases[case_id].get("passed")]
    pass_rate_delta = new_pass_rate - old_pass_rate
    reasons: list[str] = []
    if pass_rate_delta < args.min_pass_rate_delta:
        reasons.append(
            f"pass_rate_delta {pass_rate_delta:.4f} below required {args.min_pass_rate_delta:.4f}."
        )
    if len(regressed) > args.max_regressed_cases:
        reasons.append(
            f"regressed_cases {len(regressed)} exceeds allowed {args.max_regressed_cases}."
        )
    if not reasons:
        reasons.append("New report satisfies pass-rate and regression gates.")
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "compare",
        "generated_at": utc_now(),
        "decision": "ADOPT" if not reasons or reasons == ["New report satisfies pass-rate and regression gates."] else "REJECT",
        "old": {"path": str(Path(args.old_report).expanduser()), "pass_rate": old_pass_rate, "case_count": len(old_cases)},
        "new": {"path": str(Path(args.new_report).expanduser()), "pass_rate": new_pass_rate, "case_count": len(new_cases)},
        "delta": {
            "pass_rate_delta": pass_rate_delta,
            "fixed_cases": fixed,
            "regressed_cases": regressed,
        },
        "policy": {
            "min_pass_rate_delta": args.min_pass_rate_delta,
            "max_regressed_cases": args.max_regressed_cases,
        },
        "reasons": reasons,
    }
    json_path = args.json_out or default_report_path(args.project_root, "compare")
    report["report_path"] = str(write_json_report(report, json_path))
    if args.md_out:
        report["markdown_path"] = str(write_markdown_report(report, args.md_out, kind="compare"))
        write_json_report(report, json_path)
    if not args.quiet:
        print_compare_summary(report)
        print(f"report: {report['report_path']}")
    return 0 if report["decision"] == "ADOPT" else 40
