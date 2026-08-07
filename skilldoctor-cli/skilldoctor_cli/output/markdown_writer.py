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
        steps = attribution.get("steps") or []
        lines.extend(
            [
                "",
                "## Attribution",
                f"- Cause: `{attribution.get('cause')}`",
                f"- Action: `{attribution.get('action')}`",
                f"- Confidence: {_percent(attribution.get('confidence'))}",
                f"- Fault step: `{attribution.get('t_star')}`",
                f"- Fault chain: `{attribution.get('fault_chain')}`",
                "",
                attribution.get("agent_reason") or attribution.get("explanation") or "",
            ]
        )
        if steps:
            lines.extend(
                [
                    "",
                    "### Step-Level Attribution",
                    "",
                    "| Step | Source | Label | Passed | Detail |",
                    "| ---: | --- | --- | --- | --- |",
                ]
            )
            for step in steps[:10]:
                detail = str(step.get("detail") or "").replace("|", "\\|").replace("\n", " ")
                if len(detail) > 120:
                    detail = f"{detail[:119]}…"
                lines.append(
                    f"| {step.get('index')} | {step.get('source')} | {step.get('label')} | {step.get('passed')} | {detail} |"
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
        domain_quality = quality.get("domain_quality") or {}
        if domain_quality:
            lines.extend(
                [
                    "",
                    "### Domain Quality",
                    f"- Skill: `{domain_quality.get('skill_id')}`",
                    f"- Score: `{domain_quality.get('score')}`",
                    f"- Passed: `{domain_quality.get('passed')}`",
                    "",
                    "| Check | Passed | Weight | Reason |",
                    "| --- | --- | ---: | --- |",
                ]
            )
            for item in domain_quality.get("checks") or []:
                reason = str(item.get("reason") or "").replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| {item.get('name')} | {item.get('passed')} | {item.get('weight')} | {reason} |"
                )
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
    baseline = report.get("baseline") or {}
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
    if baseline.get("enabled"):
        lines.extend(
            [
                "",
                "## Baseline",
                "",
                f"- Source: `{baseline.get('source')}`",
                f"- Name: `{baseline.get('name')}`",
                f"- Path: `{baseline.get('path')}`",
            ]
        )
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


def markdown_for_label_validation(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    confusion = summary.get("filter_confusion") or {}
    quality_confusion = summary.get("quality_confusion") or {}
    lines = [
        "# Skill Doctor Label Validation",
        "",
        f"- Label set: `{report.get('label_set_path')}`",
        f"- Domain quality threshold: `{report.get('domain_quality_threshold')}`",
        f"- Total: `{summary.get('total', 0)}`",
        f"- Skipped: `{summary.get('skipped', 0)}`",
        f"- Prediction accuracy: {_percent(summary.get('prediction_accuracy'))}",
        f"- Quality accuracy: {_percent(summary.get('quality_accuracy'))}",
        f"- False accept rate: {_percent(summary.get('false_accept_rate'))}",
        "",
        "## Filter Decision Confusion",
        "",
        "| TP | TN | FP | FN |",
        "| ---: | ---: | ---: | ---: |",
        f"| {confusion.get('tp', 0)} | {confusion.get('tn', 0)} | {confusion.get('fp', 0)} | {confusion.get('fn', 0)} |",
        "",
        "## Domain Quality Confusion",
        "",
        "| True Accept | True Reject | False Accept | False Reject |",
        "| ---: | ---: | ---: | ---: |",
        f"| {quality_confusion.get('true_accept', 0)} | {quality_confusion.get('true_reject', 0)} | {quality_confusion.get('false_accept', 0)} | {quality_confusion.get('false_reject', 0)} |",
    ]
    gate = report.get("quality_gate") or {}
    if gate:
        lines.extend(["", "## Quality Gate", "", f"- Passed: `{gate.get('passed')}`"])
        failures = gate.get("failures") or []
        if failures:
            lines.extend(["", "### Gate Failures"])
            lines.extend(f"- {item.get('message')}" for item in failures)
    cases = report.get("cases") or []
    if cases:
        lines.extend(
            [
                "",
                "## Worst Domain Quality Cases",
                "",
                "| Case | Expected | Predicted | Correct | Domain Score | Quality Pass |",
                "| --- | --- | --- | --- | ---: | --- |",
            ]
        )
        for item in sorted(
            cases,
            key=lambda row: float(row.get("domain_quality_score") or 0.0),
        )[:10]:
            lines.append(
                f"| `{item.get('case_id')}` | {item.get('expected_filter')} | {item.get('predicted_filter')} | {item.get('prediction_correct')} | {item.get('domain_quality_score')} | {item.get('domain_quality_passed')} |"
            )
    return "\n".join(lines).strip() + "\n"


def markdown_for_repair_preview(report: dict[str, Any]) -> str:
    target = report.get("target") or {}
    diagnosis = report.get("diagnosis") or {}
    proposal = report.get("proposal") or {}
    mutation = report.get("mutation") or {}
    risk = report.get("risk") or {}
    validation = report.get("validation") or {}
    failed_step = diagnosis.get("failed_step") or {}
    lines = [
        "# Skill Doctor Repair Preview",
        "",
        f"- Repairable: `{report.get('repairable')}`",
        f"- Target skill: `{target.get('skill_id')}@{target.get('skill_version')}`",
        f"- Mode: `{proposal.get('mode')}`",
        f"- Risk: `{risk.get('level')}`",
        "",
        "## Diagnosis",
        "",
        f"- Cause: `{diagnosis.get('cause')}`",
        f"- Action: `{diagnosis.get('action')}`",
        f"- Fault type: `{diagnosis.get('fault_type')}`",
        f"- Fault step: `{diagnosis.get('fault_step')}`",
        f"- Fault chain: `{diagnosis.get('fault_chain')}`",
    ]
    if failed_step:
        lines.extend(
            [
                "",
                "### Failed Step Evidence",
                "",
                f"- Step: `{failed_step.get('index')}`",
                f"- Source: `{failed_step.get('source')}`",
                f"- Label: `{failed_step.get('label')}`",
                f"- Detail: {failed_step.get('detail')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Proposal",
            "",
            f"- Summary: {proposal.get('summary')}",
            f"- Suggested change: {proposal.get('suggested_change')}",
            "",
            "### Rationale",
            "",
            proposal.get("rationale") or "n/a",
            "",
            "## Mutation Policy",
            "",
            f"- Applies changes: `{mutation.get('applies_changes')}`",
            f"- Apply policy: `{mutation.get('apply_policy')}`",
            f"- Message: {mutation.get('message')}",
        ]
    )
    if mutation.get("allowed_next_actions"):
        lines.extend(["", "### Allowed Next Actions"])
        lines.extend(f"- `{action}`" for action in mutation.get("allowed_next_actions") or [])
    lines.extend(["", "## Risk"])
    for reason in risk.get("reasons") or []:
        lines.append(f"- {reason}")
    if validation.get("required"):
        lines.extend(["", "## Required Validation"])
        lines.extend(f"- `{command}`" for command in validation.get("commands") or [])
    return "\n".join(lines).strip() + "\n"


def write_markdown_report(report: dict[str, Any], path: str | Path, *, kind: str) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "compare":
        content = markdown_for_compare(report)
    elif kind == "repair_preview":
        content = markdown_for_repair_preview(report)
    elif kind == "validate_labels":
        content = markdown_for_label_validation(report)
    elif kind in {"bench", "suite"}:
        content = markdown_for_suite(report)
    else:
        content = markdown_for_run(report)
    target.write_text(content, encoding="utf-8")
    return target
