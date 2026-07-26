from __future__ import annotations

import argparse
import json
import sys

from .models import RunRequest
from .service import RunService


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Run the Skill Doctor LangGraph orchestration loop."
    )
    subcommands = command.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="Execute one agent run.")
    run.add_argument(
        "--executor",
        choices=["fixture", "replay", "codex"],
        default="fixture",
    )
    run.add_argument("--scenario", choices=["content-gap", "network-error"], default="content-gap")
    run.add_argument("--skill-id", default="tdd-workflow")
    run.add_argument("--max-attempts", type=int, default=2)
    run.add_argument("--codex-timeout-ms", type=int, default=180_000)
    run.add_argument(
        "--task",
        default="Use the target Skill to produce a verified implementation plan.",
    )
    return command


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    request = RunRequest(
        task=args.task,
        skill_id=args.skill_id,
        executor=args.executor,
        scenario=args.scenario,
        max_attempts=args.max_attempts,
        codex_timeout_ms=args.codex_timeout_ms,
    )
    result = RunService().run(request)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
