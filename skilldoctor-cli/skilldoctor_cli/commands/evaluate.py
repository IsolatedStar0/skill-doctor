from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from ..backend import backend_modules, new_run_service
from ..output.console import print_run_summary
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..quality import score_state
from ..workspace import default_report_path, load_json


def register(subcommands) -> None:
    command = subcommands.add_parser("evaluate", help="Evaluate one trace and score successful-run quality.")
    command.add_argument("trace", help="Path to a TraceIngestRequest-compatible JSON file.")
    command.add_argument("--project-root", type=Path)
    command.add_argument("--min-score", type=float, default=0.75)
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def handle(args: Namespace) -> int:
    modules = backend_modules(args.project_root)
    request = modules["TraceIngestRequest"].model_validate(load_json(args.trace))
    state = new_run_service(args.project_root).ingest_trace(request)
    quality = score_state(state)
    state["quality"] = quality
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "evaluate",
        "trace_path": str(Path(args.trace).expanduser()),
        "min_score": args.min_score,
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
        return 10
    return 0 if quality["overall_score"] >= args.min_score else 20
