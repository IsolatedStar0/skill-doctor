from __future__ import annotations

from pathlib import Path
from typing import Any


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def markdown_for_run(report: dict[str, Any]) -> str:
    state = report.get("state") or report
    execution = state.get("execution") or {}
    attribution = state.get("attribution") or {}
    quality = state.get("quality") or report.get("quality") or {}
    lines = [
        "# Skill Doctor Report",
        "",
        f"- Run ID: `{state.get('run_id')}`",
        f"- Status: `{state.get('status')}`",
        f"- Skill: `{state.get('skill_id')}@{state.get('skill_version')}`",
        f"- Pass rate: {_percent(execution.get('pass_rate'))}",
    ]
    if attribution:
        lines.extend(
            [
                "",
                "## Attribution",
                f"- Cause: `{attribution.get('cause')}`",
                f"- Action: `{attribution.get('action')}`",
                f"- Confidence: {_percent(attribution.get('confidence'))}",
                "",
                attribution.get("agent_reason") or attribution.get("explanation") or "",
            ]
        )
    if quality:
        lines.extend(
            [
                "",
                "## Quality Score",
                f"- Grade: `{quality.get('grade')}`",
                f"- Overall: `{quality.get('overall_score')}`",
                "",
                "| Dimension | Score |",
                "| --- | ---: |",
            ]
        )
        for name, score in (quality.get("dimensions") or {}).items():
            lines.append(f"| {name} | {score} |")
        if quality.get("score_breakdown"):
            lines.extend(
                [
                    "",
                    "### Score Breakdown",
                    "",
                    "| Dimension | Score | Weight | Weighted |",
                    "| --- | ---: | ---: | ---: |",
                ]
            )
            for name, item in quality["score_breakdown"].items():
                lines.append(
                    f"| {name} | {item.get('score')} | {item.get('weight')} | {item.get('weighted_score')} |"
                )
        if quality.get("reasons"):
            lines.extend(["", "### Dimension Reasons"])
            for name, reasons in quality["reasons"].items():
                if not reasons:
                    continue
                lines.append(f"- `{name}`")
                lines.extend(f"  - {reason}" for reason in reasons)
        if quality.get("evidence_refs"):
            lines.extend(["", "### Evidence References"])
            lines.extend(f"- `{item}`" for item in quality["evidence_refs"][:20])
        if quality.get("findings"):
            lines.extend(["", "### Findings"])
            lines.extend(f"- {item}" for item in quality["findings"])
    quality_gate = report.get("quality_gate") or {}
    if quality_gate:
        lines.extend(
            [
                "",
                "## Quality Gate",
                f"- Passed: `{quality_gate.get('passed')}`",
            ]
        )
        failures = quality_gate.get("failures") or []
        if failures:
            lines.extend(["", "### Gate Failures"])
            lines.extend(f"- {item.get('message')}" for item in failures)
    return "\n".join(lines).strip() + "\n"


def markdown_for_suite(report: dict[str, Any]) -> str:
    if report.get("markdown"):
        lines = [str(report["markdown"]).rstrip()]
        case_set = report.get("case_set") or {}
        skipped = case_set.get("skipped") or []
        if skipped:
            lines.extend(["", "## Skipped Cases"])
            lines.extend(
                f"- `{item.get('case_id')}`: {item.get('reason')}"
                for item in skipped
            )
        return "\n".join(lines).strip() + "\n"
    summary = report.get("summary") or {}
    lines = [
        f"# {report.get('name', 'Skill Doctor Suite')}",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Total: `{summary.get('total', 0)}`",
        f"- Passed: `{summary.get('passed', 0)}`",
        f"- Failed: `{summary.get('failed', 0)}`",
        f"- Skipped: `{summary.get('skipped', 0)}`",
        f"- Flaky included: `{summary.get('flaky', 0)}`",
        f"- Pass rate: {_percent(summary.get('pass_rate'))}",
        "",
        "| Case | Result | Category | Tags | Flaky |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in report.get("cases") or []:
        tags = ", ".join(case.get("tags") or [])
        lines.append(
            f"| `{case.get('case_id')}` | {'PASS' if case.get('passed') else 'FAIL'} | {case.get('category', '')} | {tags} | {case.get('flaky', False)} |"
        )
    skipped = (report.get("case_set") or {}).get("skipped") or []
    if skipped:
        lines.extend(["", "## Skipped Cases"])
        lines.extend(
            f"- `{item.get('case_id')}`: {item.get('reason')}"
            for item in skipped
        )
    return "\n".join(lines).strip() + "\n"


def markdown_for_compare(report: dict[str, Any]) -> str:
    lines = [
        "# Skill Doctor Compare",
        "",
        f"- Decision: `{report.get('decision')}`",
        f"- Old pass rate: {_percent(report.get('old', {}).get('pass_rate'))}",
        f"- New pass rate: {_percent(report.get('new', {}).get('pass_rate'))}",
        f"- Delta: {_percent(report.get('delta', {}).get('pass_rate_delta'))}",
        "",
        "## Reasons",
    ]
    lines.extend(f"- {reason}" for reason in report.get("reasons", []))
    regressed = report.get("delta", {}).get("regressed_cases") or []
    if regressed:
        lines.extend(["", "## Regressed Cases"])
        lines.extend(f"- `{case_id}`" for case_id in regressed)
    return "\n".join(lines).strip() + "\n"


def write_markdown_report(report: dict[str, Any], path: str | Path, *, kind: str) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "compare":
        content = markdown_for_compare(report)
    elif kind in {"bench", "suite"}:
        content = markdown_for_suite(report)
    else:
        content = markdown_for_run(report)
    target.write_text(content, encoding="utf-8")
    return target
