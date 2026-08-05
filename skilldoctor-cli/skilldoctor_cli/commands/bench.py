from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from ..backend import backend_modules, new_run_service
from ..output.console import print_suite_summary
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..quality import score_state
from ..workspace import default_report_path, load_jsonl, utc_now


def register(subcommands) -> None:
    command = subcommands.add_parser("bench", help="Run a JSONL case set through the backend diagnostic pipeline.")
    command.add_argument("cases", help="JSONL file; each line is DiagnosticCaseRequest or TraceIngestRequest shape.")
    command.add_argument("--project-root", type=Path)
    command.add_argument("--suite-id", default="cli-bench")
    command.add_argument("--name", default="CLI Bench Suite")
    command.add_argument("--include-default-cases", action="store_true")
    command.add_argument("--include-saved-cases", action="store_true")
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def _as_case(modules: dict[str, Any], payload: dict[str, Any], index: int):
    DiagnosticCaseRequest = modules["DiagnosticCaseRequest"]
    if "trace" in payload:
        return DiagnosticCaseRequest.model_validate(payload)
    return DiagnosticCaseRequest.model_validate(
        {
            "case_id": payload.get("case_id") or f"cli-case-{index:03d}",
            "name": payload.get("name") or payload.get("task") or f"CLI case {index}",
            "description": payload.get("description", "Imported from CLI JSONL case set."),
            "source": payload.get("source", "custom"),
            "trace": payload,
            "expectation": payload.get("expectation", {}),
        }
    )


def handle(args: Namespace) -> int:
    modules = backend_modules(args.project_root)
    cases = [_as_case(modules, payload, index) for index, payload in enumerate(load_jsonl(args.cases), start=1)]
    request = modules["DiagnosticSuiteRequest"](
        suite_id=args.suite_id,
        name=args.name,
        include_default_cases=args.include_default_cases,
        include_saved_cases=args.include_saved_cases,
        cases=cases,
    )
    service = new_run_service(args.project_root)
    report = service.run_diagnostic_suite(request)
    quality_scores: list[float] = []
    for case in report.get("cases", []):
        run_id = case.get("run_id")
        if not run_id:
            continue
        try:
            quality_scores.append(score_state(service.get(run_id))["overall_score"])
        except (FileNotFoundError, ValueError, KeyError):
            continue
    if quality_scores:
        report["summary"]["quality_average"] = round(sum(quality_scores) / len(quality_scores), 4)
    report["kind"] = "bench"
    report["case_set_path"] = str(Path(args.cases).expanduser())
    report["generated_at"] = report.get("generated_at") or utc_now()
    json_path = args.json_out or default_report_path(args.project_root, "bench")
    report["report_path"] = str(write_json_report(report, json_path))
    if args.md_out:
        report["markdown_path"] = str(write_markdown_report(report, args.md_out, kind="bench"))
        write_json_report(report, json_path)
    if not args.quiet:
        print_suite_summary(report, title="Skill Doctor Bench")
        print(f"report: {report['report_path']}")
    return 0 if report.get("status") == "passed" else 30
