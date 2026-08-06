from __future__ import annotations

from argparse import Namespace, SUPPRESS
from pathlib import Path
from typing import Any

from ..backend import backend_modules, new_run_service
from ..exit_codes import EXIT_DIAGNOSIS_FAILED, EXIT_OK
from ..output.console import print_run_summary
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..sources import IngestInput, SUPPORTED_SOURCES, get_adapter
from ..workspace import default_report_path


def register(subcommands) -> None:
    command = subcommands.add_parser(
        "ingest",
        help="Ingest an external agent-platform trace through a source adapter.",
    )
    command.add_argument("trace", nargs="?", help="Path to a source trace JSON file.")
    command.add_argument(
        "--source",
        required=True,
        choices=SUPPORTED_SOURCES,
        help=f"Trace source adapter. Currently supports: {', '.join(SUPPORTED_SOURCES)}.",
    )
    command.add_argument("--project-root", type=Path, default=SUPPRESS)
    command.add_argument(
        "--trace-dir",
        type=Path,
        help=(
            "Directory containing raw platform trace channels. For AIME, supported files are "
            "metadata.json, runtime_events.jsonl/json, tool_calls.jsonl/json, "
            "model_messages.jsonl/json, business_result.json, and skill_content.md. "
            "The generic source supports the same standard trace-dir protocol."
        ),
    )
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def _load_source_payload(args: Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    adapter = get_adapter(args.source)
    loaded = adapter.load(IngestInput(trace=args.trace, trace_dir=args.trace_dir))
    return loaded.payload, loaded.input_info


def handle(args: Namespace) -> int:
    adapter = get_adapter(args.source)
    payload, source_input = _load_source_payload(args)
    adapted_payload = adapter.adapt(payload)
    modules = backend_modules(args.project_root)
    request = modules["TraceIngestRequest"].model_validate(adapted_payload)
    state = new_run_service(args.project_root).ingest_trace(request)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "diagnose",
        "ingest": {
            "source": args.source,
            "adapter": adapter.adapter_name,
            **source_input,
        },
        "state": state,
    }
    json_path = args.json_out or default_report_path(args.project_root, f"ingest-{args.source}")
    report["report_path"] = str(write_json_report(report, json_path))
    if args.md_out:
        report["markdown_path"] = str(write_markdown_report(report, args.md_out, kind="diagnose"))
        write_json_report(report, json_path)
    if not args.quiet:
        print_run_summary(state, title=f"Skill Doctor Ingest ({args.source})")
        print(f"report: {report['report_path']}")
        if report.get("markdown_path"):
            print(f"markdown: {report['markdown_path']}")
    return EXIT_OK if state.get("status") == "passed" else EXIT_DIAGNOSIS_FAILED
