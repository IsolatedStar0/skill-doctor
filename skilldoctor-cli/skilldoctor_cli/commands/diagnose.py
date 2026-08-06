from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any

from ..backend import backend_modules, new_run_service
from ..exit_codes import EXIT_DIAGNOSIS_FAILED, EXIT_OK
from ..output.console import print_run_summary
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..workspace import default_report_path, load_json


def register(subcommands) -> None:
    command = subcommands.add_parser("diagnose", help="Diagnose one local trace file.")
    command.add_argument("trace", help="Path to a TraceIngestRequest-compatible JSON file.")
    command.add_argument("--project-root", type=Path)
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def handle(args: Namespace) -> int:
    modules = backend_modules(args.project_root)
    request = modules["TraceIngestRequest"].model_validate(load_json(args.trace))
    service = new_run_service(args.project_root)
    state = service.ingest_trace(request)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "diagnose",
        "trace_path": str(Path(args.trace).expanduser()),
        "state": state,
    }
    json_path = args.json_out or default_report_path(args.project_root, "diagnose")
    report["report_path"] = str(write_json_report(report, json_path))
    if args.md_out:
        report["markdown_path"] = str(write_markdown_report(report, args.md_out, kind="diagnose"))
        write_json_report(report, json_path)
    if not args.quiet:
        print_run_summary(state, title="Skill Doctor Diagnose")
        print(f"report: {report['report_path']}")
        if report.get("markdown_path"):
            print(f"markdown: {report['markdown_path']}")
    return EXIT_OK if state.get("status") == "passed" else EXIT_DIAGNOSIS_FAILED
