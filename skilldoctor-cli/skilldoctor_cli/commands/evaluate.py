from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from ..backend import backend_modules, new_run_service
from ..exit_codes import EXIT_DIAGNOSIS_FAILED, EXIT_OK, EXIT_QUALITY_GATE_FAILED
from ..output.console import print_run_summary
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..quality import score_state
from ..workspace import default_report_path, load_json


DIMENSION_OPTIONS = {
    "output_quality": "min_output_quality",
    "contract_compliance": "min_contract_compliance",
    "evidence_support": "min_evidence_support",
    "cost_efficiency": "min_cost_efficiency",
    "safety_boundary": "min_safety_boundary",
    "stability": "min_stability",
    "domain_quality": "min_domain_quality",
}


def register(subcommands) -> None:
    command = subcommands.add_parser("evaluate", help="Evaluate one trace and score successful-run quality.")
    command.add_argument("trace", help="Path to a TraceIngestRequest-compatible JSON file.")
    command.add_argument("--project-root", type=Path)
    command.add_argument("--min-score", type=float, default=0.75)
    command.add_argument("--min-output-quality", type=float)
    command.add_argument("--min-contract-compliance", type=float)
    command.add_argument("--min-evidence-support", type=float)
    command.add_argument("--min-cost-efficiency", type=float)
    command.add_argument("--min-safety-boundary", type=float)
    command.add_argument("--min-stability", type=float)
    command.add_argument("--min-domain-quality", type=float)
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def _dimension_thresholds(args: Namespace) -> dict[str, float]:
    thresholds: dict[str, float] = {}
    for dimension, option_name in DIMENSION_OPTIONS.items():
        value = getattr(args, option_name, None)
        if value is not None:
            thresholds[dimension] = float(value)
    return thresholds


def _quality_gate_failures(
    quality: dict[str, Any],
    *,
    min_score: float,
    dimension_thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    overall = float(quality.get("overall_score") or 0.0)
    if overall < min_score:
        failures.append(
            {
                "name": "overall_score",
                "expected": min_score,
                "actual": overall,
                "message": f"overall_score {overall:.4f} is below required {min_score:.4f}.",
            }
        )
    dimensions = quality.get("dimensions") or {}
    for dimension, threshold in dimension_thresholds.items():
        actual = float(dimensions.get(dimension) or 0.0)
        if actual < threshold:
            failures.append(
                {
                    "name": dimension,
                    "expected": threshold,
                    "actual": actual,
                    "message": f"{dimension} {actual:.4f} is below required {threshold:.4f}.",
                }
            )
    return failures


def handle(args: Namespace) -> int:
    modules = backend_modules(args.project_root)
    request = modules["TraceIngestRequest"].model_validate(load_json(args.trace))
    state = new_run_service(args.project_root).ingest_trace(request)
    quality = score_state(state)
    state["quality"] = quality
    dimension_thresholds = _dimension_thresholds(args)
    gate_failures = _quality_gate_failures(
        quality,
        min_score=args.min_score,
        dimension_thresholds=dimension_thresholds,
    )
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "evaluate",
        "trace_path": str(Path(args.trace).expanduser()),
        "min_score": args.min_score,
        "dimension_thresholds": dimension_thresholds,
        "quality_gate": {
            "passed": not gate_failures and state.get("status") == "passed",
            "failures": gate_failures,
        },
        "quality": quality,
        "state": state,
    }
    json_path = args.json_out or default_report_path(args.project_root, "evaluate")
    report["report_path"] = str(write_json_report(report, json_path))
    if args.md_out:
        report["markdown_path"] = str(write_markdown_report(report, args.md_out, kind="evaluate"))
        write_json_report(report, json_path)
    if not args.quiet:
        print_run_summary(state, title="Skill Doctor Evaluate")
        print(f"report: {report['report_path']}")
    if state.get("status") != "passed":
        return EXIT_DIAGNOSIS_FAILED
    return EXIT_OK if not gate_failures else EXIT_QUALITY_GATE_FAILED
