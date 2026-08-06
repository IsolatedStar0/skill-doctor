from __future__ import annotations

from argparse import Namespace, SUPPRESS
from pathlib import Path
from typing import Any

from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..workspace import default_report_path, load_json, utc_now


def register(subcommands) -> None:
    command = subcommands.add_parser(
        "repair-preview",
        help="Generate an auditable repair preview from a diagnose/evaluate JSON report.",
    )
    command.add_argument("json_report", help="JSON report produced by diagnose or evaluate.")
    command.add_argument("--project-root", type=Path, default=SUPPRESS)
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def _state_from_report(report: dict[str, Any]) -> dict[str, Any]:
    state = report.get("state") or report
    if not isinstance(state, dict):
        raise ValueError("repair-preview requires a diagnose/evaluate style report with a state object.")
    return state


def _failed_step(attribution: dict[str, Any]) -> dict[str, Any] | None:
    steps = [step for step in attribution.get("steps") or [] if isinstance(step, dict)]
    t_star = attribution.get("t_star")
    for step in steps:
        if t_star is not None and step.get("index") == t_star:
            return step
    for step in steps:
        if step.get("passed") is False:
            return step
    return steps[0] if steps else None


def _repair_mode(attribution: dict[str, Any]) -> str:
    action = attribution.get("action")
    fault_type = attribution.get("fault_type")
    if action == "patch_loader" or fault_type == "skill_missing":
        return "generate"
    if action == "patch_skill":
        return "revise"
    return "none"


def _risk(mode: str, attribution: dict[str, Any], failed_step: dict[str, Any] | None) -> dict[str, Any]:
    if mode == "none":
        return {
            "level": "low",
            "reasons": ["No skill mutation is recommended for this report."],
        }
    reasons = ["Skill behavior may change and should be validated against the baseline suite."]
    if attribution.get("fault_type") == "skill_missing":
        reasons.append("Generating new skill guidance can overfit the failing trajectory if not benchmarked.")
        level = "high"
    else:
        level = "medium"
    if failed_step:
        reasons.append(f"Fault evidence is anchored at step {failed_step.get('index')} ({failed_step.get('label')}).")
    return {"level": level, "reasons": reasons}


def _suggested_change(mode: str, attribution: dict[str, Any], failed_step: dict[str, Any] | None) -> str:
    principle = str(attribution.get("improvement_principle") or "").strip()
    if principle:
        return principle
    if failed_step and failed_step.get("detail"):
        verb = "Create skill guidance to handle" if mode == "generate" else "Revise the skill to prevent"
        return f"{verb}: {failed_step.get('detail')}"
    if mode == "generate":
        return "Create missing skill guidance for the localized failure pattern."
    if mode == "revise":
        return "Revise the existing skill according to the localized failure evidence."
    return "No skill repair is recommended."


def _build_preview(report: dict[str, Any], source_path: Path) -> dict[str, Any]:
    state = _state_from_report(report)
    attribution = state.get("attribution") or {}
    if not isinstance(attribution, dict):
        attribution = {}
    failed_step = _failed_step(attribution)
    mode = _repair_mode(attribution)
    repairable = mode != "none" and attribution.get("cause") in {"skill", "loader"}
    suggested_change = _suggested_change(mode, attribution, failed_step)
    risk = _risk(mode if repairable else "none", attribution, failed_step)
    validation_commands = [
        "skilldoctor bench <cases.jsonl> --include-tag release --json-out <bench-report.json>",
        "skilldoctor compare --baseline-name main <bench-report.json>",
    ]
    return {
        "schema_version": "1.0",
        "kind": "repair_preview",
        "generated_at": utc_now(),
        "source_report": str(source_path.expanduser()),
        "repairable": repairable,
        "target": {
            "skill_id": state.get("skill_id"),
            "skill_version": state.get("skill_version"),
        },
        "diagnosis": {
            "cause": attribution.get("cause"),
            "action": attribution.get("action"),
            "fault_type": attribution.get("fault_type"),
            "fault_step": attribution.get("t_star"),
            "fault_chain": attribution.get("fault_chain") or [],
            "failed_step": failed_step,
            "evidence_refs": attribution.get("evidence_refs") or [],
        },
        "proposal": {
            "mode": mode if repairable else "none",
            "summary": suggested_change,
            "rationale": attribution.get("agent_reason") or attribution.get("explanation") or "",
            "suggested_change": suggested_change,
        },
        "mutation": {
            "applies_changes": False,
            "apply_policy": "manual_review_required",
            "message": "repair-preview is read-only; it does not modify skills or project files.",
            "allowed_next_actions": [
                "review_preview",
                "run_required_validation",
                "create_manual_skill_patch",
            ]
            if repairable
            else ["route_to_non_skill_owner"],
        },
        "risk": risk,
        "validation": {
            "required": repairable,
            "commands": validation_commands if repairable else [],
        },
    }


def handle(args: Namespace) -> int:
    source_path = Path(args.json_report)
    report = load_json(source_path)
    preview = _build_preview(report, source_path)
    json_path = args.json_out or default_report_path(args.project_root, "repair-preview")
    preview["report_path"] = str(write_json_report(preview, json_path))
    if args.md_out:
        preview["markdown_path"] = str(write_markdown_report(preview, args.md_out, kind="repair_preview"))
        write_json_report(preview, json_path)
    if not args.quiet:
        print(f"repairable: {preview['repairable']}")
        print(f"mode: {preview['proposal']['mode']}")
        print(f"report: {preview['report_path']}")
        if preview.get("markdown_path"):
            print(f"markdown: {preview['markdown_path']}")
    return 0
