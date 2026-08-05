from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from ..output.markdown_writer import write_markdown_report
from ..workspace import default_report_path, load_json


def register(subcommands) -> None:
    command = subcommands.add_parser("report", help="Render an existing Skill Doctor JSON report as Markdown.")
    command.add_argument("json_report", help="JSON report produced by diagnose/evaluate/bench/compare.")
    command.add_argument("--project-root", type=Path)
    command.add_argument("--kind", choices=["diagnose", "evaluate", "bench", "suite", "compare"], default=None)
    command.add_argument("--md-out", type=Path)
    command.set_defaults(handler=handle)


def handle(args: Namespace) -> int:
    report = load_json(args.json_report)
    kind = args.kind or report.get("kind") or "diagnose"
    md_path = args.md_out or default_report_path(args.project_root, "report", suffix="md")
    written = write_markdown_report(report, md_path, kind=kind)
    print(f"markdown: {written}")
    return 0
