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
    case_diff = report.get("case_diff") or {}
    quality = report.get("quality") or {}
    cost = report.get("cost") or {}
    lines = [
        "# Skill Doctor Compare",
        "",
        f"- Decision: `{report.get('decision')}`",
        f"- Old pass rate: {_percent(report.get('old', {}).get('pass_rate'))}",
        f"- New pass rate: {_percent(report.get('new', {}).get('pass_rate'))}",
        f"- Delta: {_percent(report.get('delta', {}).get('pass_rate_delta'))}",
        f"- Quality delta: `{report.get('delta', {}).get('quality_delta')}`",
        f"- Safety delta: `{report.get('delta', {}).get('safety_boundary_delta')}`",
        "",
        "## Reasons",
    ]
    lines.extend(f"- {reason}" for reason in report.get("reasons", []))
    gate_summary = report.get("gate_summary") or {}
    if gate_summary:
        lines.extend(
            [
                "",
                "## CI Gate Summary",
                "",
                f"- Passed: `{gate_summary.get('passed')}`",
                f"- Failure count: `{gate_summary.get('failure_count')}`",
                f"- Regressed cases: `{gate_summary.get('regressed_case_count')}`",
                f"- New skill failures: `{gate_summary.get('new_skill_failure_count')}`",
            ]
        )
    gate_failures = report.get("gate_failures") or []
    if gate_failures:
        lines.extend(
            [
                "",
                "## CI Gate Failures",
                "",
                "| Gate | Actual | Expected | Message |",
                "| --- | ---: | ---: | --- |",
            ]
        )
        for item in gate_failures:
            lines.append(
                f"| {item.get('name')} | {item.get('actual')} | {item.get('expected')} | {item.get('message')} |"
            )
    if quality:
        lines.extend(
            [
                "",
                "## Quality Diff",
                "",
                "| Metric | Old | New | Delta |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        old_overall = (quality.get("old") or {}).get("overall")
        new_overall = (quality.get("new") or {}).get("overall")
        delta = report.get("delta", {}).get("quality_delta")
        lines.append(f"| overall | {old_overall} | {new_overall} | {delta} |")
        old_dimensions = (quality.get("old") or {}).get("dimensions") or {}
        new_dimensions = (quality.get("new") or {}).get("dimensions") or {}
        for name in sorted(set(old_dimensions) | set(new_dimensions)):
            old_value = old_dimensions.get(name)
            new_value = new_dimensions.get(name)
            dim_delta = None
            if old_value is not None and new_value is not None:
                dim_delta = round(float(new_value) - float(old_value), 4)
            lines.append(f"| {name} | {old_value} | {new_value} | {dim_delta} |")
    if cost:
        lines.extend(
            [
                "",
                "## Cost Diff",
                "",
                "| Metric | Old | New | Rate Delta |",
                "| --- | ---: | ---: | ---: |",
                f"| tokens | {(cost.get('old') or {}).get('tokens')} | {(cost.get('new') or {}).get('tokens')} | {report.get('delta', {}).get('token_increase_rate')} |",
                f"| duration_ms | {(cost.get('old') or {}).get('duration_ms')} | {(cost.get('new') or {}).get('duration_ms')} | {report.get('delta', {}).get('duration_increase_rate')} |",
            ]
        )
    rows = case_diff.get("case_rows") or []
    if rows:
        lines.extend(
            [
                "",
                "## Case Diff",
                "",
                "| Case | Status | Old | New | Category | Cause |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in rows:
            lines.append(
                f"| `{item.get('case_id')}` | {item.get('status')} | {item.get('old_passed')} | {item.get('new_passed')} | {item.get('category')} | {item.get('cause')} |"
            )
    regressed = case_diff.get("regressed_cases") or report.get("delta", {}).get("regressed_cases") or []
    if regressed:
        lines.extend(["", "## Regressed Cases"])
        lines.extend(f"- `{case_id}`" for case_id in regressed)
    new_failures = case_diff.get("new_failures") or []
    if new_failures:
        lines.extend(["", "## New Failures"])
        lines.extend(f"- `{case_id}`" for case_id in new_failures)
    fixed = case_diff.get("fixed_cases") or []
    if fixed:
        lines.extend(["", "## Fixed Cases"])
        lines.extend(f"- `{case_id}`" for case_id in fixed)
    persistent = case_diff.get("persistent_failures") or []
    if persistent:
        lines.extend(["", "## Persistent Failures"])
        lines.extend(f"- `{case_id}`" for case_id in persistent)
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
