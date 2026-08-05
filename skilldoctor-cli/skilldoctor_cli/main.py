from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .commands import bench, compare, diagnose, evaluate, report
from .workspace import default_project_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skilldoctor",
        description="CLI-first local Skill Doctor for diagnosing traces and gating Skill quality.",
    )
    parser.add_argument("--version", action="version", version=f"skilldoctor {__version__}")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=default_project_root(),
        help="Skill Doctor repo root. Defaults to the parent of skilldoctor-cli.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    for module in (diagnose, evaluate, bench, compare, report):
        module.register(subcommands)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    # argparse parent options are not automatically copied into subparsers when
    # placed before command definitions, so normalize command-local values here.
    if not getattr(args, "project_root", None):
        args.project_root = default_project_root()
    args.project_root = Path(args.project_root).expanduser().resolve()
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"skilldoctor: error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
