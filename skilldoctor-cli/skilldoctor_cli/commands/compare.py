from __future__ import annotations

from argparse import Namespace, SUPPRESS
from pathlib import Path
from typing import Any

from ..exit_codes import EXIT_COMPARE_REJECTED, EXIT_OK
from ..output.console import print_compare_summary
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..workspace import baseline_report_path, default_report_path, load_json, utc_now


def register(subcommands) -> None:
    command = subcommands.add_parser("compare", help="Compare old/new bench or evaluation reports for regressions.")
    command.add_argument(
        "old_report",
        help="Old JSON report path, or new report path when --baseline/--baseline-name/auto baseline is used.",
    )
    command.add_argument("new_report", nargs="?", help="New JSON report path.")
    command.add_argument("--project-root", type=Path, default=SUPPRESS)
    command.add_argument("--baseline", type=Path, help="Baseline JSON report path used as the old report.")
    command.add_argument(
        "--baseline-name",
        default=None,
        help="Named baseline under <project-root>/.skilldoctor/baselines/<name>.json.",
    )
    command.add_argument("--min-pass-rate-delta", type=float, default=0.0)
    command.add_argument("--max-regressed-cases", type=int, default=0)
    command.add_argument("--max-quality-drop", type=float, default=0.0)
    command.add_argument("--max-cost-increase-rate", type=float, default=1.0)
    command.add_argument("--max-safety-drop", type=float, default=0.0)
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def _case_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if report.get("state"):
        state = report["state"]
        quality = state.get("quality") or report.get("quality") or {}
        execution = state.get("execution") or {}
        usage = execution.get("usage") or {}
        case_id = state.get("case_id") or state.get("skill_id") or state.get("run_id", "single-run")
        return {
            str(case_id): {
                "case_id": str(case_id),
                "passed": state.get("status") == "passed",
                "status": state.get("status"),
                "quality_score": quality.get("overall_score"),
                "quality_dimensions": quality.get("dimensions") or {},
                "cost": _cost_from_usage(usage),
                "duration_ms": execution.get("duration_ms"),
                "attribution": state.get("attribution") or {},
            }
        }
    return {str(case.get("case_id")): case for case in report.get("cases") or []}


def _cost_from_usage(usage: dict[str, Any]) -> int:
    return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)


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


def _quality_summary(report: dict[str, Any], cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    quality = report.get("quality") or (report.get("state") or {}).get("quality") or {}
    dimensions = quality.get("dimensions") or {}
    scores = [float(item["quality_score"]) for item in cases.values() if item.get("quality_score") is not None]
    if not quality and not scores:
        return {"overall": None, "dimensions": {}}
    return {
        "overall": quality.get("overall_score") if quality else round(sum(scores) / len(scores), 4),
        "dimensions": dimensions,
    }


def _cost_summary(report: dict[str, Any], cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    state = report.get("state") or {}
    execution = state.get("execution") or {}
    usage = execution.get("usage") or {}
    if usage or execution.get("duration_ms") is not None:
        return {
            "tokens": _cost_from_usage(usage),
            "duration_ms": int(execution.get("duration_ms") or 0),
        }
    tokens = [int(item.get("cost") or 0) for item in cases.values() if item.get("cost") is not None]
    durations = [int(item.get("duration_ms") or 0) for item in cases.values() if item.get("duration_ms") is not None]
    return {
        "tokens": sum(tokens) if tokens else None,
        "duration_ms": sum(durations) if durations else None,
    }


def _rate_delta(old: float | int | None, new: float | int | None) -> float | None:
    if old is None or new is None:
        return None
    if float(old) == 0:
        return None if float(new) == 0 else 1.0
    return (float(new) - float(old)) / float(old)


def _safety_score(report: dict[str, Any]) -> float | None:
    quality = report.get("quality") or (report.get("state") or {}).get("quality") or {}
    dimensions = quality.get("dimensions") or {}
    if "safety_boundary" in dimensions:
        return float(dimensions["safety_boundary"])
    return None


def _case_diff(
    old_cases: dict[str, dict[str, Any]],
    new_cases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    old_ids = set(old_cases)
    new_ids = set(new_cases)
    common_ids = sorted(old_ids & new_ids)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    fixed = [case_id for case_id in common_ids if not old_cases[case_id].get("passed") and new_cases[case_id].get("passed")]
    regressed = [case_id for case_id in common_ids if old_cases[case_id].get("passed") and not new_cases[case_id].get("passed")]
    persistent_failures = [
        case_id for case_id in common_ids if not old_cases[case_id].get("passed") and not new_cases[case_id].get("passed")
    ]
    new_failures = [case_id for case_id in added if not new_cases[case_id].get("passed")]
    new_skill_failures = [
        case_id
        for case_id in new_failures
        if (new_cases[case_id].get("attribution") or {}).get("cause") == "skill"
    ]
    case_rows = []
    for case_id in sorted(old_ids | new_ids):
        old_case = old_cases.get(case_id)
        new_case = new_cases.get(case_id)
        if old_case and new_case:
            if old_case.get("passed") and not new_case.get("passed"):
                status = "regressed"
            elif not old_case.get("passed") and new_case.get("passed"):
                status = "fixed"
            elif not old_case.get("passed") and not new_case.get("passed"):
                status = "persistent_failure"
            else:
                status = "unchanged_pass"
        elif new_case:
            status = "new_failure" if not new_case.get("passed") else "new_pass"
        else:
            status = "removed"
        case_rows.append(
            {
                "case_id": case_id,
                "status": status,
                "old_passed": old_case.get("passed") if old_case else None,
                "new_passed": new_case.get("passed") if new_case else None,
                "category": (new_case or old_case or {}).get("category"),
                "cause": ((new_case or old_case or {}).get("attribution") or {}).get("cause"),
            }
        )
    return {
        "added_cases": added,
        "removed_cases": removed,
        "fixed_cases": fixed,
        "regressed_cases": regressed,
        "persistent_failures": persistent_failures,
        "new_failures": new_failures,
        "new_skill_failures": new_skill_failures,
        "case_rows": case_rows,
    }


def _gate_failure(name: str, message: str, *, actual: Any, expected: Any) -> dict[str, Any]:
    return {
        "name": name,
        "severity": "blocking",
        "actual": actual,
        "expected": expected,
        "message": message,
    }


def _resolve_report_paths(args: Namespace) -> tuple[Path, Path, dict[str, Any]]:
    if args.baseline and args.baseline_name:
        raise SystemExit("compare accepts either --baseline or --baseline-name, not both.")
    if args.baseline:
        return (
            args.baseline,
            Path(args.new_report or args.old_report),
            {
                "enabled": True,
                "path": str(Path(args.baseline).expanduser()),
                "source": "explicit",
            },
        )
    if args.baseline_name:
        try:
            baseline_path = baseline_report_path(Path(args.project_root), args.baseline_name)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        if not baseline_path.exists():
            raise SystemExit(f"named baseline not found: {baseline_path}")
        return (
            baseline_path,
            Path(args.new_report or args.old_report),
            {
                "enabled": True,
                "path": str(baseline_path.expanduser()),
                "source": "named",
                "name": args.baseline_name.strip(),
            },
        )
    if not args.new_report:
        baseline_name = "main"
        baseline_path = baseline_report_path(Path(args.project_root), baseline_name)
        if not baseline_path.exists():
            raise SystemExit(
                "compare requires old_report and new_report, --baseline, --baseline-name, "
                f"or an auto baseline at {baseline_path}."
            )
        return (
            baseline_path,
            Path(args.old_report),
            {
                "enabled": True,
                "path": str(baseline_path.expanduser()),
                "source": "auto",
                "name": baseline_name,
            },
        )
    return (
        Path(args.old_report),
        Path(args.new_report),
        {"enabled": False, "path": None, "source": None},
    )


def handle(args: Namespace) -> int:
    old_report_path, new_report_path, baseline = _resolve_report_paths(args)
    old_report = load_json(old_report_path)
    new_report = load_json(new_report_path)
    old_cases = _case_map(old_report)
    new_cases = _case_map(new_report)
    old_pass_rate = _pass_rate(old_report, old_cases)
    new_pass_rate = _pass_rate(new_report, new_cases)
    case_diff = _case_diff(old_cases, new_cases)
    pass_rate_delta = new_pass_rate - old_pass_rate
    old_quality = _quality_summary(old_report, old_cases)
    new_quality = _quality_summary(new_report, new_cases)
    quality_delta = None
    if old_quality["overall"] is not None and new_quality["overall"] is not None:
        quality_delta = float(new_quality["overall"]) - float(old_quality["overall"])
    old_cost = _cost_summary(old_report, old_cases)
    new_cost = _cost_summary(new_report, new_cases)
    token_increase_rate = _rate_delta(old_cost.get("tokens"), new_cost.get("tokens"))
    duration_increase_rate = _rate_delta(old_cost.get("duration_ms"), new_cost.get("duration_ms"))
    safety_delta = None
    old_safety = _safety_score(old_report)
    new_safety = _safety_score(new_report)
    if old_safety is not None and new_safety is not None:
        safety_delta = new_safety - old_safety
    reasons: list[str] = []
    gate_failures: list[dict[str, Any]] = []
    if pass_rate_delta < args.min_pass_rate_delta:
        message = f"pass_rate_delta {pass_rate_delta:.4f} below required {args.min_pass_rate_delta:.4f}."
        reasons.append(message)
        gate_failures.append(
            _gate_failure(
                "pass_rate_delta",
                message,
                actual=pass_rate_delta,
                expected=args.min_pass_rate_delta,
            )
        )
    if len(case_diff["regressed_cases"]) > args.max_regressed_cases:
        message = f"regressed_cases {len(case_diff['regressed_cases'])} exceeds allowed {args.max_regressed_cases}."
        reasons.append(message)
        gate_failures.append(
            _gate_failure(
                "regressed_cases",
                message,
                actual=len(case_diff["regressed_cases"]),
                expected=args.max_regressed_cases,
            )
        )
    if case_diff["new_skill_failures"]:
        message = f"new_skill_failures detected: {', '.join(case_diff['new_skill_failures'])}."
        reasons.append(message)
        gate_failures.append(
            _gate_failure(
                "new_skill_failures",
                message,
                actual=len(case_diff["new_skill_failures"]),
                expected=0,
            )
        )
    if quality_delta is not None and quality_delta < -args.max_quality_drop:
        message = f"quality_delta {quality_delta:.4f} below allowed drop {-args.max_quality_drop:.4f}."
        reasons.append(message)
        gate_failures.append(
            _gate_failure(
                "quality_delta",
                message,
                actual=quality_delta,
                expected=-args.max_quality_drop,
            )
        )
    if token_increase_rate is not None and token_increase_rate > args.max_cost_increase_rate:
        message = f"token_increase_rate {token_increase_rate:.4f} exceeds allowed {args.max_cost_increase_rate:.4f}."
        reasons.append(message)
        gate_failures.append(
            _gate_failure(
                "token_increase_rate",
                message,
                actual=token_increase_rate,
                expected=args.max_cost_increase_rate,
            )
        )
    if duration_increase_rate is not None and duration_increase_rate > args.max_cost_increase_rate:
        message = f"duration_increase_rate {duration_increase_rate:.4f} exceeds allowed {args.max_cost_increase_rate:.4f}."
        reasons.append(message)
        gate_failures.append(
            _gate_failure(
                "duration_increase_rate",
                message,
                actual=duration_increase_rate,
                expected=args.max_cost_increase_rate,
            )
        )
    if safety_delta is not None and safety_delta < -args.max_safety_drop:
        message = f"safety_boundary_delta {safety_delta:.4f} below allowed drop {-args.max_safety_drop:.4f}."
        reasons.append(message)
        gate_failures.append(
            _gate_failure(
                "safety_boundary_delta",
                message,
                actual=safety_delta,
                expected=-args.max_safety_drop,
            )
        )
    if not reasons:
        reasons.append("New report satisfies pass-rate and regression gates.")
    gate_summary = {
        "passed": not gate_failures,
        "failure_count": len(gate_failures),
        "regressed_case_count": len(case_diff["regressed_cases"]),
        "new_failure_count": len(case_diff["new_failures"]),
        "new_skill_failure_count": len(case_diff["new_skill_failures"]),
        "quality_delta": quality_delta,
        "safety_boundary_delta": safety_delta,
        "token_increase_rate": token_increase_rate,
        "duration_increase_rate": duration_increase_rate,
    }
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "compare",
        "generated_at": utc_now(),
        "decision": "ADOPT" if not reasons or reasons == ["New report satisfies pass-rate and regression gates."] else "REJECT",
        "old": {"path": str(Path(old_report_path).expanduser()), "pass_rate": old_pass_rate, "case_count": len(old_cases)},
        "new": {"path": str(Path(new_report_path).expanduser()), "pass_rate": new_pass_rate, "case_count": len(new_cases)},
        "baseline": baseline,
        "delta": {
            "pass_rate_delta": pass_rate_delta,
            "fixed_cases": case_diff["fixed_cases"],
            "regressed_cases": case_diff["regressed_cases"],
            "quality_delta": quality_delta,
            "safety_boundary_delta": safety_delta,
            "token_increase_rate": token_increase_rate,
            "duration_increase_rate": duration_increase_rate,
        },
        "case_diff": case_diff,
        "quality": {"old": old_quality, "new": new_quality},
        "cost": {"old": old_cost, "new": new_cost},
        "gate_summary": gate_summary,
        "gate_failures": gate_failures,
        "blocking_regressions": {
            "regressed_cases": case_diff["regressed_cases"],
            "new_skill_failures": case_diff["new_skill_failures"],
        },
        "policy": {
            "min_pass_rate_delta": args.min_pass_rate_delta,
            "max_regressed_cases": args.max_regressed_cases,
            "max_quality_drop": args.max_quality_drop,
            "max_cost_increase_rate": args.max_cost_increase_rate,
            "max_safety_drop": args.max_safety_drop,
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
    return EXIT_OK if report["decision"] == "ADOPT" else EXIT_COMPARE_REJECTED
