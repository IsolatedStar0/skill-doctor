from __future__ import annotations

import shutil
from argparse import Namespace, SUPPRESS
from pathlib import Path

from ..workspace import baseline_report_path, baseline_reports_dir, load_json, utc_now


def register(subcommands) -> None:
    command = subcommands.add_parser("baseline", help="Manage local compare baselines.")
    command.add_argument("--project-root", type=Path, default=SUPPRESS)
    baseline_commands = command.add_subparsers(dest="baseline_command", required=True)

    save = baseline_commands.add_parser("save", help="Save a JSON report as a named compare baseline.")
    save.add_argument("json_report", help="JSON report produced by bench/evaluate/compare.")
    save.add_argument("--name", default="main", help="Baseline name. Defaults to main.")
    save.add_argument("--force", action="store_true", help="Overwrite an existing baseline.")
    save.set_defaults(handler=handle_save)

    list_command = baseline_commands.add_parser("list", help="List saved compare baselines.")
    list_command.set_defaults(handler=handle_list)


def _resolve_baseline_path(project_root: Path, name: str) -> Path:
    try:
        return baseline_report_path(project_root, name)
    except ValueError as error:
        raise SystemExit(str(error)) from error


def handle_save(args: Namespace) -> int:
    source = Path(args.json_report).expanduser()
    report = load_json(source)
    target = _resolve_baseline_path(args.project_root, args.name)
    if target.exists() and not args.force:
        raise ValueError(f"baseline already exists: {target}. Use --force to overwrite.")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"baseline saved: {target}")
    print(f"source kind: {report.get('kind')}")
    print(f"saved at: {utc_now()}")
    return 0


def handle_list(args: Namespace) -> int:
    root = baseline_reports_dir(args.project_root)
    if not root.exists():
        print("No baselines found.")
        return 0
    baselines = sorted(root.glob("*.json"))
    if not baselines:
        print("No baselines found.")
        return 0
    print("Baselines:")
    for path in baselines:
        try:
            report = load_json(path)
        except Exception:
            report = {}
        summary = report.get("summary") or {}
        print(
            f"- {path.stem}: {path}"
            f" kind={report.get('kind')}"
            f" pass_rate={summary.get('pass_rate')}"
        )
    return 0
